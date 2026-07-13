"""Verify camera-specific still and video ROI selection."""

from pi import capture, trigger_server


def test_still_command_uses_explicit_camera_roi(monkeypatch):
    monkeypatch.setattr(capture, 'find_camera_still_command', lambda: 'rpicam-still')
    monkeypatch.setattr(capture, 'CAMERA_SENSOR_MODE', '')
    monkeypatch.setattr(capture, 'CAMERA_FOCUS_MODE', '')
    command = capture.build_still_command(1280, 720, 80, camera_index=1, roi='0.2,0.1,0.5,0.6')
    assert command[command.index('--roi') + 1] == '0.2,0.1,0.5,0.6'
    assert command[command.index('--camera') + 1] == '1'


def test_video_roi_is_selected_by_camera_index():
    settings = {
        'day_camera_index': 0,
        'night_camera_index': 1,
        'video_roi': '0,0,1,1',
        'day_video_roi': '0.1,0.1,0.5,0.5',
        'night_video_roi': '0.2,0.2,0.6,0.6',
    }
    assert trigger_server.roi_for_camera(settings, 0) == '0.1,0.1,0.5,0.5'
    assert trigger_server.roi_for_camera(settings, 1) == '0.2,0.2,0.6,0.6'


def test_still_roi_falls_back_to_legacy_setting():
    settings = {
        'day_camera_index': 0,
        'night_camera_index': 1,
        'camera_roi': '0.05,0.15,0.4,0.4',
    }
    assert trigger_server.roi_for_camera(settings, 1, kind='still') == '0.05,0.15,0.4,0.4'
