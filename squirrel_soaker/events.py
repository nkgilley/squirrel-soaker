"""Event classification helpers shared by dashboard statistics and tests."""


def counts_as_blasted_squirrel(classification):
    """Return whether a spray event should count as a blasted squirrel."""
    return classification != 'false_positive'
