from dataclasses import dataclass


@dataclass(frozen=True)
class Opened:
    detected_at: float
    reason: str


@dataclass(frozen=True)
class Closed:
    detected_at: float
    last_motion_at: float
    reason: str


class EventTracker:
    """Turns a per-frame yes/no trigger into open and close signals.

    Takes timestamps as arguments and never reads a clock, so tests can replay time.
    """

    def __init__(self, post_roll, max_event):
        self.post_roll = post_roll
        self.max_event = max_event
        self._open = None
        self._last_active = None

    @property
    def is_open(self):
        return self._open is not None

    def update(self, timestamp, active, reason="motion"):
        if self._open is None:
            if not active:
                return None
            self._open = Opened(timestamp, reason)
            self._last_active = timestamp
            return self._open

        if active:
            self._last_active = timestamp

        quiet_for = timestamp - self._last_active
        too_long = timestamp - self._open.detected_at >= self.max_event
        if quiet_for >= self.post_roll or too_long:
            return self._close()
        return None

    def flush(self):
        return self._close() if self._open is not None else None

    def _close(self):
        opened = self._open
        closed = Closed(opened.detected_at, self._last_active, opened.reason)
        self._open = None
        self._last_active = None
        return closed
