"""Verify event classifications produce honest dashboard statistics."""

from squirrel_soaker.events import counts_as_blasted_squirrel


def test_false_positive_does_not_count_as_blasted_squirrel():
    assert not counts_as_blasted_squirrel('false_positive')


def test_unreviewed_and_accurate_events_still_count():
    assert counts_as_blasted_squirrel(None)
    assert counts_as_blasted_squirrel('accurate')
