"""Dependency-free helpers shared by model training and unit tests."""

import os
import random
import re


def checkpoint_filename(period, timestamp, suffix=None):
    """Build a checkpoint name that identifies its training dataset period."""
    label = 'shared' if period == 'all' else period
    if label not in ('shared', 'day', 'night'):
        raise ValueError('period must be all, shared, day, or night')
    suffix_part = '_{0}'.format(suffix) if suffix is not None else ''
    return 'resnet18_{0}_{1}{2}.pth'.format(label, timestamp, suffix_part)


def sample_group(path):
    """Group adjacent captures so burst frames cannot cross the split."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.startswith('false_alarm_'):
        return re.sub(r'_\d+$', '', stem)
    match = re.search(r'(\d{8})_(\d{6})', stem)
    if match:
        date_part, time_part = match.groups()
        minute_bucket = time_part[:4]
        return '{0}_{1}'.format(date_part, minute_bucket)
    return re.sub(r'_\d+$', '', stem)


def grouped_split_indices(samples, validation_fraction=0.2, seed=42):
    """Return train/validation indexes split by capture group."""
    groups = {}
    for index, (path, _label) in enumerate(samples):
        groups.setdefault(sample_group(path), []).append(index)

    group_names = sorted(groups)
    random.Random(seed).shuffle(group_names)
    target = max(1, int(round(len(samples) * validation_fraction)))
    validation_groups = set()
    validation_count = 0
    for group_name in group_names:
        if validation_count >= target and validation_groups:
            break
        validation_groups.add(group_name)
        validation_count += len(groups[group_name])

    val_indices = [index for name in validation_groups for index in groups[name]]
    train_indices = [index for name in group_names if name not in validation_groups for index in groups[name]]
    if not train_indices or not val_indices:
        raise ValueError('Need at least two capture groups for grouped validation')
    return train_indices, val_indices


def classification_metrics(labels, predictions, classes):
    matrix = [[0 for _ in classes] for _ in classes]
    for actual, predicted in zip(labels, predictions):
        matrix[int(actual)][int(predicted)] += 1

    total = sum(sum(row) for row in matrix)
    correct = sum(matrix[i][i] for i in range(len(classes)))
    per_class = {}
    for index, name in enumerate(classes):
        true_positive = matrix[index][index]
        false_positive = sum(row[index] for row in matrix) - true_positive
        false_negative = sum(matrix[index]) - true_positive
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[name] = {
            'precision': round(precision, 6),
            'recall': round(recall, 6),
            'f1': round(f1, 6),
            'support': sum(matrix[index]),
        }
    return {
        'accuracy': round(correct / total, 6) if total else 0.0,
        'total': total,
        'confusion_matrix': matrix,
        'per_class': per_class,
    }
