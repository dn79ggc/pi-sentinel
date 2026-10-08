import time
from datetime import date


class Throttle:
    """Allows an event only after the cooldown and while under the daily cap."""

    def __init__(self, cooldown, daily_cap, clock=time.monotonic, today=date.today):
        self.cooldown = cooldown
        self.daily_cap = daily_cap
        self._clock = clock
        self._today = today
        self._day = today()
        self._count = 0
        self._last = None

    def allow(self):
        now = self._clock()
        today = self._today()
        if today != self._day:
            self._day = today
            self._count = 0
        if self._last is not None and now - self._last < self.cooldown:
            return False
        if self._count >= self.daily_cap:
            return False
        self._last = now
        self._count += 1
        return True
