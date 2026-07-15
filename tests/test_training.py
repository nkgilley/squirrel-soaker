"""Verify dependency-free training splits, grouping, and evaluation metrics."""

from squirrel_soaker.training import checkpoint_filename, classification_metrics, grouped_split_indices, sample_group


def test_checkpoint_filename_identifies_training_period():
    assert checkpoint_filename('day', '20260715_143012') == 'resnet18_day_20260715_143012.pth'
    assert checkpoint_filename('night', '20260715_143012') == 'resnet18_night_20260715_143012.pth'
    assert checkpoint_filename('all', '20260715_143012', 2) == 'resnet18_shared_20260715_143012_2.pth'


def test_capture_groups_bucket_burst_frames_together():
    assert sample_group('/tmp/img_auto_20260712_120501_123456.jpg') == '20260712_1205'
    assert sample_group('/tmp/false_alarm_vid_20260712_120501_01.jpg') == 'false_alarm_vid_20260712_120501'


def test_grouped_split_never_separates_a_capture_group():
    samples = [
        ('img_auto_20260712_120501_000001.jpg', 0),
        ('img_auto_20260712_120502_000002.jpg', 0),
        ('img_auto_20260712_120601_000003.jpg', 1),
        ('img_auto_20260712_120602_000004.jpg', 1),
        ('img_auto_20260712_120701_000005.jpg', 0),
        ('img_auto_20260712_120702_000006.jpg', 1),
    ]
    train, validation = grouped_split_indices(samples, validation_fraction=0.34, seed=7)
    assert set(train).isdisjoint(validation)
    for group in {sample_group(path) for path, _ in samples}:
        group_indexes = {index for index, (path, _) in enumerate(samples) if sample_group(path) == group}
        assert not (group_indexes & set(train) and group_indexes & set(validation))


def test_metrics_report_squirrel_recall_and_confusion_matrix():
    metrics = classification_metrics([0, 0, 1, 1], [0, 1, 1, 1], ['not_squirrel', 'squirrel'])
    assert metrics['accuracy'] == 0.75
    assert metrics['per_class']['squirrel']['recall'] == 1.0
    assert metrics['confusion_matrix'] == [[1, 1], [0, 2]]
