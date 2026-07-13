"""Thread-safe in-memory health history used by the web application."""

import threading
from collections import deque


class HealthStore:
    """Bounded health sample store with isolated synchronization."""

    def __init__(self, max_samples=720):
        self._samples = deque(maxlen=max(1, int(max_samples)))
        self._lock = threading.Lock()

    def add(self, sample):
        with self._lock:
            self._samples.append(dict(sample))

    def since(self, cutoff):
        with self._lock:
            return [dict(sample) for sample in self._samples if sample.get('t', 0) >= cutoff]

    def snapshot(self):
        with self._lock:
            return [dict(sample) for sample in self._samples]
