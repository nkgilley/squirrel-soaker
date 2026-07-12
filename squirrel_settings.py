"""Server-side validation for settings received from the web API."""


NUMERIC_BOUNDS = {
    'analysis_interval': (5, 300),
    'save_interval': (5, 3600),
    'daylight_latitude': (-90, 90),
    'daylight_longitude': (-180, 180),
    'sunrise_offset_minutes': (-180, 180),
    'sunset_offset_minutes': (-180, 180),
    'daylight_start_hour': (0, 23),
    'daylight_end_hour': (0, 23),
    'analysis_width': (320, 1920),
    'analysis_height': (240, 1440),
    'review_width': (640, 4608),
    'review_height': (480, 2592),
    'analysis_jpeg_quality': (30, 95),
    'review_jpeg_quality': (50, 100),
    'motion_threshold': (0, 100),
    'motion_force_interval': (5, 600),
    'day_camera_index': (0, 3),
    'night_camera_index': (0, 3),
    'confidence_threshold': (0.5, 0.999),
    'spray_confirmation_timeout_seconds': (15, 900),
    'spray_decision_required_hits': (1, 10),
    'spray_decision_window_seconds': (1, 300),
    'spray_decision_average_confidence': (0.5, 0.999),
    'spray_cooldown_seconds': (0, 3600),
    'spray_duration': (0.05, 10),
    'long_spray_duration': (0.05, 10),
    'long_spray_threshold_hours': (0, 168),
    'retention_days_raw': (0.1, 3650),
    'retention_days_not_squirrel': (0.1, 3650),
    'retention_min_not_squirrel': (0, 1000000),
    'retention_days_trash': (0.1, 3650),
    'retention_days_videos': (0.1, 3650),
    'camera_lens_position': (0, 32),
    'camera_saturation': (0, 4),
    'camera_contrast': (0, 4),
    'camera_sharpness': (0, 4),
}

CHOICES = {
    'daylight_mode': {'sun', 'fixed'},
    'camera_focus_mode': {'', 'manual', 'auto', 'continuous'},
    'spray_mode': {'auto', 'confirm'},
    'notification_type': {'none', 'join', 'email', 'both'},
    'spray_controller_type': {'pi', 'esphome'},
    'camera_source': {'pi', 'snapshot', 'rtsp'},
}


def validate_settings_patch(data, defaults, maximum_spray_seconds=10.0):
    if not isinstance(data, dict):
        return {}, ['settings payload must be a JSON object']

    normalized = {}
    errors = []
    for key, value in data.items():
        if key not in defaults:
            errors.append('unknown setting: {0}'.format(key))
            continue

        default = defaults[key]
        try:
            if isinstance(default, bool):
                if isinstance(value, str):
                    normalized[key] = value.strip().lower() in ('1', 'true', 'yes', 'on')
                else:
                    normalized[key] = bool(value)
            elif isinstance(default, int) and not isinstance(default, bool):
                normalized[key] = int(value)
            elif isinstance(default, float):
                normalized[key] = float(value)
            elif value is None:
                normalized[key] = ''
            else:
                normalized[key] = str(value).strip()
        except (TypeError, ValueError):
            errors.append('{0} has an invalid value'.format(key))
            continue

        if key in NUMERIC_BOUNDS:
            minimum, maximum = NUMERIC_BOUNDS[key]
            if key in ('spray_duration', 'long_spray_duration'):
                maximum = min(maximum, float(maximum_spray_seconds))
            if not minimum <= normalized[key] <= maximum:
                errors.append('{0} must be between {1} and {2}'.format(key, minimum, maximum))

        if key in CHOICES and normalized[key] not in CHOICES[key]:
            errors.append('{0} must be one of: {1}'.format(key, ', '.join(sorted(CHOICES[key]))))

    if errors:
        return {}, errors
    return normalized, []


def public_device_settings(settings):
    """Remove secrets before returning configuration to a device agent."""
    secret_keys = {
        'gemini_api_key',
        'join_api_key',
        'email_smtp_server',
        'email_to',
        'public_base_url',
    }
    return {key: value for key, value in settings.items() if key not in secret_keys}
