#!/usr/bin/env python3
import os
import sys
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms, models
from torch.utils.data import DataLoader, random_split

def train_model():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATASET_DIR = os.path.join(BASE_DIR, 'data', 'dataset')
    
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
        transforms.ColorJitter(brightness=0.1, contrast=0.1),
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
        
    dataset = datasets.ImageFolder(DATASET_DIR)
    classes = dataset.classes
    print("Classes found: {0}".format(classes))
    
    if len(classes) < 2:
        print("Error: Need at least 2 classes (squirrel and not_squirrel) to train.")
        sys.exit(1)
        
    # 4. Split dataset into train (80%) and validation (20%) sets
    val_size = int(len(dataset) * 0.2)
    train_size = len(dataset) - val_size
    train_set, val_set = random_split(dataset, [train_size, val_size])
    
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
    
    num_epochs = 10
    best_val_acc = 0.0
    model_path = os.path.join(BASE_DIR, 'model.pth')
    
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
        val_corrects = 0
        
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs = inputs.to(device)
                labels = labels.to(device)
                
                outputs = model(inputs)
                _, preds = torch.max(outputs, 1)
                loss = criterion(outputs, labels)
                
                val_loss += loss.item() * inputs.size(0)
                val_corrects += torch.sum(preds == labels.data)
                
        val_loss = val_loss / len(val_dataset)
        val_acc = val_corrects.float() / len(val_dataset)
        
        print("Epoch {0}/{1} - Train Loss: {2:.4f} Acc: {3:.4f} | Val Loss: {4:.4f} Acc: {5:.4f}".format(
            epoch + 1, num_epochs, epoch_loss, epoch_acc, val_loss, val_acc
        ))
        
        # Save best model
        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            torch.save({
                'model_state_dict': model.state_dict(),
                'classes': classes
            }, model_path)
            
    print("Training finished! Best validation accuracy: {0:.4f}".format(best_val_acc))
    print("Saved model checkpoint to {0}".format(model_path))

if __name__ == '__main__':
    train_model()
