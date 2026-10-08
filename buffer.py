import threading
from collections import deque


class FrameBuffer:
    """Holds recent JPEG frames in memory so a clip can include the seconds before motion."""

    def __init__(self, max_seconds):
        self.max_seconds = max_seconds
        self._frames = deque()
        self._lock = threading.Lock()

    def add(self, timestamp, jpeg):
        with self._lock:
            self._frames.append((timestamp, jpeg))
            cutoff = timestamp - self.max_seconds
            while self._frames and self._frames[0][0] < cutoff:
                self._frames.popleft()

    def window(self, start, end):
        with self._lock:
            return [(ts, jpeg) for ts, jpeg in self._frames if start <= ts <= end]

    def __len__(self):
        with self._lock:
            return len(self._frames)
