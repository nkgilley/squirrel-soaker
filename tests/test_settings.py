"""Verify settings normalization, validation, and secret filtering."""

from squirrel_soaker.settings import public_device_settings, validate_settings_patch


DEFAULTS = {
    'analysis_interval': 5,
    'spray_duration': 3.0,
    'motion_prefilter_enabled': True,
    'nighttime_mode_enabled': True,
    'spray_mode': 'auto',
    'camera_roi': '',
    'day_camera_roi': '',
    'night_camera_roi': '',
    'day_video_roi': '',
    'night_video_roi': '',
    'gemini_api_key': '',
    'join_api_key': '',
    'email_to': '',
    'ir_camera_plug_enabled': False,
    'ir_camera_plug_ip': '',
}


def test_settings_are_typed_and_validated():
    values, errors = validate_settings_patch(
        {
            'analysis_interval': '10',
            'spray_duration': '4.5',
            'motion_prefilter_enabled': 'false',
            'nighttime_mode_enabled': 'false',
            'spray_mode': 'confirm',
        },
        DEFAULTS,
        maximum_spray_seconds=10,
    )
    assert not errors
    assert values == {
        'analysis_interval': 10,
        'spray_duration': 4.5,
        'motion_prefilter_enabled': False,
        'nighttime_mode_enabled': False,
        'spray_mode': 'confirm',
    }


def test_invalid_or_unknown_settings_are_rejected_as_a_unit():
    values, errors = validate_settings_patch(
        {'analysis_interval': 1, 'spray_duration': 20, 'unknown': True},
        DEFAULTS,
        maximum_spray_seconds=10,
    )
    assert values == {}
    assert len(errors) == 3


def test_device_settings_do_not_expose_secrets():
    settings = {
        'analysis_interval': 5,
        'gemini_api_key': 'secret',
        'join_api_key': 'secret',
        'email_to': 'private@example.com',
    }
    assert public_device_settings(settings) == {'analysis_interval': 5}


def test_ir_plug_requires_a_valid_ip_when_enabled():
    values, errors = validate_settings_patch(
        {'ir_camera_plug_enabled': True, 'ir_camera_plug_ip': 'not-an-ip'},
        DEFAULTS,
    )
    assert values == {}
    assert 'ir_camera_plug_ip must be a valid IP address' in errors


def test_ir_plug_ip_is_accepted_when_enabled():
    values, errors = validate_settings_patch(
        {'ir_camera_plug_enabled': True, 'ir_camera_plug_ip': '192.0.2.50'},
        DEFAULTS,
    )
    assert not errors
    assert values['ir_camera_plug_ip'] == '192.0.2.50'


def test_camera_specific_rois_are_validated():
    values, errors = validate_settings_patch(
        {
            'day_camera_roi': '0.1,0.2,0.5,0.6',
            'night_camera_roi': '0,0,1,1',
            'day_video_roi': '',
            'night_video_roi': '0.25,0.25,0.5,0.5',
        },
        DEFAULTS,
    )
    assert not errors
    assert values['night_camera_roi'] == '0,0,1,1'


def test_camera_specific_roi_cannot_extend_past_sensor_bounds():
    values, errors = validate_settings_patch(
        {'night_camera_roi': '0.75,0.2,0.5,0.5'},
        DEFAULTS,
    )
    assert values == {}
    assert 'night_camera_roi must be empty or x,y,width,height values contained within 0-1' in errors
