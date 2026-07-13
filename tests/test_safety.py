"""Verify spray limits, duration bounds, and device authentication helpers."""

import math

import pytest

from squirrel_soaker.safety import DetectionGate, SprayBudget, bounded_duration, device_auth_headers, device_token_matches


def test_duration_is_finite_and_bounded():
    assert bounded_duration(-5, maximum=10) == 0.05
    assert bounded_duration(999, maximum=10) == 10
    assert bounded_duration(float('inf'), default=3, maximum=10) == 3
    assert bounded_duration(math.nan, default=4, maximum=10) == 4
    assert bounded_duration('invalid', default=2.5, maximum=10) == 2.5


def test_device_bearer_token_requires_an_exact_match():
    token = 'a' * 32
    headers = device_auth_headers(token)
    assert device_token_matches(headers['Authorization'], token)
    assert not device_token_matches('Bearer wrong', token)
    assert not device_token_matches('', token)
    assert not device_token_matches('Bearer anything', '')


def test_spray_budget_limits_count_and_open_time():
    budget = SprayBudget(max_count=2, max_open_seconds=5, window_seconds=60)
    assert budget.check(2, now=100) == (True, None)
    budget.record(2, now=100)
    assert budget.check(2, now=101) == (True, None)
    budget.record(2, now=101)
    allowed, reason = budget.check(1, now=102)
    assert not allowed
    assert 'count' in reason
    assert budget.check(2, now=161) == (True, None)


def test_spray_budget_rejects_excess_open_time():
    budget = SprayBudget(max_count=10, max_open_seconds=5, window_seconds=60)
    budget.record(4, now=100)
    allowed, reason = budget.check(2, now=101)
    assert not allowed
    assert 'open-time' in reason


def test_detection_gate_requires_repeated_hits_and_clears_after_ready():
    gate = DetectionGate()
    first = gate.evaluate(True, 0.9, 0.8, 10, 2, 0.85, now=100)
    second = gate.evaluate(True, 0.8, 0.8, 10, 2, 0.85, now=101)
    third = gate.evaluate(False, 0.1, 0.8, 10, 2, 0.85, now=102)
    assert not first['ready']
    assert second['ready']
    assert second['average_confidence'] == pytest.approx(0.85)
    assert third['hits'] == 0


def test_detection_gate_expires_old_hits():
    gate = DetectionGate()
    gate.evaluate(True, 0.95, 0.8, 10, 2, 0.8, now=100)
    result = gate.evaluate(True, 0.95, 0.8, 10, 2, 0.8, now=111)
    assert not result['ready']
    assert result['hits'] == 1
