#!/usr/bin/env python3
"""Train and evaluate day, night, or shared squirrel classifiers with PyTorch."""

import os
import sys
import argparse
import datetime
import json
import random
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms, models
from torch.utils.data import DataLoader, Subset
from PIL import ImageFile
from squirrel_training import classification_metrics, grouped_split_indices
ImageFile.LOAD_TRUNCATED_IMAGES = True


class SafeImageFolder(datasets.ImageFolder):
    def __init__(self, root):
        super().__init__(root)
        self.samples = [
            (path, label)
            for path, label in self.samples
            if os.path.exists(path)
        ]
        self.imgs = self.samples

    def __getitem__(self, index):
        attempts = 0
        dataset_len = len(self.samples)
        if dataset_len == 0:
            raise IndexError("No training samples available")

        while attempts < dataset_len:
            try:
                return super().__getitem__(index % dataset_len)
            except (FileNotFoundError, OSError) as e:
                path = self.samples[index % dataset_len][0]
                print("Warning: skipping unreadable training image {0}: {1}".format(path, e), flush=True)
                index += 1
                attempts += 1

        raise RuntimeError("All candidate training images were missing or unreadable")


def choose_dataset(base_dir, period):
    period_dir = os.path.join(base_dir, 'data', 'dataset_{0}'.format(period))
    common_dir = os.path.join(base_dir, 'data', 'dataset')
    if os.path.isdir(period_dir):
        return period_dir
    return common_dir


def train_model(period='all', seed=42, epochs=10):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    if period not in ('all', 'day', 'night'):
        raise ValueError('period must be all, day, or night')
    DATASET_DIR = choose_dataset(BASE_DIR, period) if period != 'all' else os.path.join(BASE_DIR, 'data', 'dataset')
    random.seed(seed)
    torch.manual_seed(seed)
    
    # 1. Set device: use MPS (Metal Performance Shaders) on Apple Silicon,
    # CUDA on NVIDIA GPUs, otherwise fallback to CPU.
    if torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")
    print("Using device: {0}".format(device))
    
    # 2. Set up image transformations with data augmentation for training
    train_transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(15),
        # Stronger brightness, contrast, saturation, and hue variations to ignore sun position/lighting shifts
        transforms.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.4, hue=0.1),
        # Randomly convert images to grayscale (20% chance) to reduce color dependency
        transforms.RandomGrayscale(p=0.2),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    val_transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    # 3. Load dataset from the folder structure (expects squirrel/ and not_squirrel/)
    if not os.path.exists(DATASET_DIR):
        print("Error: Dataset directory {0} does not exist. Train aborted.".format(DATASET_DIR))
        sys.exit(1)
        
    dataset = SafeImageFolder(DATASET_DIR)
    classes = dataset.classes
    print("Classes found: {0}".format(classes))
    
    if len(classes) < 2:
        print("Error: Need at least 2 classes (squirrel and not_squirrel) to train.")
        sys.exit(1)
        
    # Split by capture group, not individual image, to prevent burst leakage.
    train_indices, val_indices = grouped_split_indices(dataset.samples, seed=seed)
    train_set = Subset(dataset, train_indices)
    val_set = Subset(dataset, val_indices)
    
    # Custom Dataset class to apply different transforms to train/val subsets
    class SubsetWrapper(torch.utils.data.Dataset):
        def __init__(self, subset, transform=None):
            self.subset = subset
            self.transform = transform
            
        def __getitem__(self, index):
            x, y = self.subset[index]
            if self.transform:
                x = self.transform(x)
            return x, y
            
        def __len__(self):
            return len(self.subset)
            
    train_dataset = SubsetWrapper(train_set, train_transform)
    val_dataset = SubsetWrapper(val_set, val_transform)
    
    train_loader = DataLoader(train_dataset, batch_size=16, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=16, shuffle=False)
    
    print("Training samples: {0}, Validation samples: {1}".format(len(train_dataset), len(val_dataset)))
    
    # 5. Load pre-trained ResNet-18 model
    print("Loading pre-trained ResNet-18 model...")
    model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
    
    # Replace the final linear classification layer (binary classifier)
    num_ftrs = model.fc.in_features
    model.fc = nn.Linear(num_ftrs, len(classes))
    model = model.to(device)
    
    # Compute class weights to address data imbalance (e.g. 34 squirrels vs 976 not_squirrels)
    class_counts = [0] * len(classes)
    for _, label in dataset.samples:
        class_counts[label] += 1
    
    total_samples = sum(class_counts)
    class_weights = [total_samples / (len(classes) * count) if count > 0 else 1.0 for count in class_counts]
    class_weights_tensor = torch.tensor(class_weights, dtype=torch.float).to(device)
    print("Class counts: {0}, Calculated weights: {1}".format(dict(zip(classes, class_counts)), class_weights))
    
    criterion = nn.CrossEntropyLoss(weight=class_weights_tensor)
    optimizer = optim.Adam(model.parameters(), lr=0.0001)
    
    num_epochs = max(1, int(epochs))
    best_val_acc = 0.0
    model_path = os.path.join(BASE_DIR, 'model.pth')
    best_metrics = None
    
    print("Starting training loop...")
    for epoch in range(num_epochs):
        # Training Phase
        model.train()
        running_loss = 0.0
        running_corrects = 0
        
        for inputs, labels in train_loader:
            inputs = inputs.to(device)
            labels = labels.to(device)
            
            optimizer.zero_grad()
            outputs = model(inputs)
            _, preds = torch.max(outputs, 1)
            loss = criterion(outputs, labels)
            
            loss.backward()
            optimizer.step()
            
            running_loss += loss.item() * inputs.size(0)
            running_corrects += torch.sum(preds == labels.data)
            
        epoch_loss = running_loss / len(train_dataset)
        epoch_acc = running_corrects.float() / len(train_dataset)
        
        # Validation Phase
        model.eval()
        val_loss = 0.0
        val_labels = []
        val_predictions = []
        
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs = inputs.to(device)
                labels = labels.to(device)
                
                outputs = model(inputs)
                _, preds = torch.max(outputs, 1)
                loss = criterion(outputs, labels)
                
                val_loss += loss.item() * inputs.size(0)
                val_labels.extend(labels.detach().cpu().tolist())
                val_predictions.extend(preds.detach().cpu().tolist())
                
        val_loss = val_loss / len(val_dataset)
        best_metrics = classification_metrics(val_labels, val_predictions, classes)
        val_acc = best_metrics['accuracy']
        
        print("Epoch {0}/{1} - Train Loss: {2:.4f} Acc: {3:.4f} | Val Loss: {4:.4f} Acc: {5:.4f}".format(
            epoch + 1, num_epochs, epoch_loss, epoch_acc, val_loss, val_acc
        ))
        
        # Save best model
        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            torch.save({
                'model_state_dict': model.state_dict(),
                'classes': classes,
                'metadata': {
                    'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'period': period,
                    'seed': seed,
                    'epochs': num_epochs,
                    'dataset_dir': os.path.relpath(DATASET_DIR, BASE_DIR),
                    'split': 'grouped_by_capture_minute_or_video',
                    'train_samples': len(train_dataset),
                    'validation_samples': len(val_dataset),
                    'metrics': best_metrics,
                }
            }, model_path)
            
    print("Training finished! Best validation accuracy: {0:.4f}".format(best_val_acc))
    print("Validation metrics: {0}".format(json.dumps(best_metrics, sort_keys=True)))
    print("Saved model checkpoint to {0}".format(model_path))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train the squirrel classifier')
    parser.add_argument('--period', choices=('all', 'day', 'night'), default='all')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--epochs', type=int, default=10)
    args = parser.parse_args()
    train_model(period=args.period, seed=args.seed, epochs=args.epochs)
