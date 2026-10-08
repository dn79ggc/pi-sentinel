from datetime import date, timedelta

from throttle import Throttle


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_blocks_events_inside_cooldown():
    clock = Clock()
    throttle = Throttle(cooldown=30, daily_cap=10, clock=clock)
    assert throttle.allow() is True
    clock.now += 29
    assert throttle.allow() is False
    clock.now += 2
    assert throttle.allow() is True


def test_blocks_after_daily_cap():
    clock = Clock()
    throttle = Throttle(cooldown=1, daily_cap=2, clock=clock)
    results = []
    for _ in range(4):
        results.append(throttle.allow())
        clock.now += 5
    assert results == [True, True, False, False]


def test_cap_resets_on_a_new_day():
    clock = Clock()
    day = {"value": date(2026, 10, 6)}
    throttle = Throttle(cooldown=1, daily_cap=1, clock=clock, today=lambda: day["value"])
    assert throttle.allow() is True
    clock.now += 5
    assert throttle.allow() is False
    day["value"] += timedelta(days=1)
    assert throttle.allow() is True
