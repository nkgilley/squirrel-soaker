from squirrel_settings import public_device_settings, validate_settings_patch


DEFAULTS = {
    'analysis_interval': 5,
    'spray_duration': 3.0,
    'motion_prefilter_enabled': True,
    'spray_mode': 'auto',
    'camera_roi': '',
    'gemini_api_key': '',
    'join_api_key': '',
    'email_to': '',
}


def test_settings_are_typed_and_validated():
    values, errors = validate_settings_patch(
        {
            'analysis_interval': '10',
            'spray_duration': '4.5',
            'motion_prefilter_enabled': 'false',
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
