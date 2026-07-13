"""Shared safety and device-authentication primitives.

This module intentionally uses only the Python standard library so the Mac
server, Raspberry Pi agent, and isolated unit tests can all import it.
"""

import hmac
import math
import threading
import time
from collections import deque


DEFAULT_MIN_SPRAY_SECONDS = 0.05
DEFAULT_MAX_SPRAY_SECONDS = 10.0


def bounded_duration(
    value,
    default=3.0,
    minimum=DEFAULT_MIN_SPRAY_SECONDS,
    maximum=DEFAULT_MAX_SPRAY_SECONDS,
):
    """Return a finite spray duration constrained to the hardware-safe range."""
    try:
        duration = float(value)
    except (TypeError, ValueError):
        duration = float(default)

    if not math.isfinite(duration):
        duration = float(default)
    return max(float(minimum), min(duration, float(maximum)))


def bearer_token_from_header(header_value):
    if not header_value:
        return ""
    scheme, separator, value = str(header_value).partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return value.strip()


def device_token_matches(header_value, expected_token):
    """Compare bearer tokens without leaking useful timing information."""
    expected = str(expected_token or "")
    supplied = bearer_token_from_header(header_value)
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def device_auth_headers(token):
    token = str(token or "").strip()
    return {"Authorization": "Bearer {0}".format(token)} if token else {}


class SprayBudget:
    """Thread-safe rolling safety budget for physical valve activations."""

    def __init__(self, max_count=30, max_open_seconds=120.0, window_seconds=3600.0):
        self.max_count = max(1, int(max_count))
        self.max_open_seconds = max(0.1, float(max_open_seconds))
        self.window_seconds = max(1.0, float(window_seconds))
        self._events = deque()
        self._lock = threading.Lock()

    def _prune(self, now):
        cutoff = now - self.window_seconds
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    def check(self, duration, now=None):
        now = time.time() if now is None else float(now)
        duration = float(duration)
        with self._lock:
            self._prune(now)
            count = len(self._events)
            open_seconds = sum(event_duration for _, event_duration in self._events)
            if count >= self.max_count:
                return False, "spray count safety budget exhausted"
            if open_seconds + duration > self.max_open_seconds:
                return False, "spray open-time safety budget exhausted"
            return True, None

    def record(self, duration, now=None):
        now = time.time() if now is None else float(now)
        with self._lock:
            self._prune(now)
            self._events.append((now, float(duration)))

    def snapshot(self, now=None):
        now = time.time() if now is None else float(now)
        with self._lock:
            self._prune(now)
            return {
                "window_seconds": self.window_seconds,
                "count": len(self._events),
                "max_count": self.max_count,
                "open_seconds": round(sum(duration for _, duration in self._events), 3),
                "max_open_seconds": self.max_open_seconds,
            }


class DetectionGate:
    """Thread-safe repeated-detection gate used before automatic spraying."""

    def __init__(self):
        self._history = deque()
        self._lock = threading.Lock()

    def evaluate(
        self,
        is_squirrel,
        confidence,
        threshold,
        window_seconds,
        required_hits,
        average_threshold,
        now=None,
    ):
        now = time.time() if now is None else float(now)
        confidence = float(confidence)
        threshold = float(threshold)
        window_seconds = max(1.0, float(window_seconds))
        required_hits = max(1, int(required_hits))
        average_threshold = float(average_threshold)

        with self._lock:
            while self._history and now - self._history[0]['t'] > window_seconds:
                self._history.popleft()
            if is_squirrel and confidence >= threshold:
                self._history.append({'t': now, 'confidence': confidence})

            qualifying = list(self._history)
            average_confidence = (
                sum(item['confidence'] for item in qualifying) / len(qualifying)
                if qualifying
                else 0.0
            )
            ready = len(qualifying) >= required_hits and average_confidence >= average_threshold
            if ready:
                self._history.clear()

        return {
            'ready': ready,
            'hits': len(qualifying),
            'required_hits': required_hits,
            'average_confidence': average_confidence,
            'average_threshold': average_threshold,
            'window_seconds': window_seconds,
        }

    def clear(self):
        with self._lock:
            self._history.clear()
