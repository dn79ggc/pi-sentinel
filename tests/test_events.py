from events import Closed, EventTracker, Opened


def feed(tracker, samples):
    """samples: (timestamp, active) pairs. Returns the non-empty signals."""
    signals = []
    for timestamp, active in samples:
        signal = tracker.update(timestamp, active)
        if signal:
            signals.append(signal)
    return signals


def test_opens_on_first_motion_only_once():
    tracker = EventTracker(post_roll=5, max_event=60)
    signals = feed(tracker, [(0, False), (1, True), (2, True), (3, True)])
    assert signals == [Opened(1, "motion")]


def test_closes_after_post_roll_of_quiet():
    tracker = EventTracker(post_roll=5, max_event=60)
    samples = [(0, True), (1, True)] + [(t, False) for t in range(2, 8)]
    signals = feed(tracker, samples)
    assert signals == [Opened(0, "motion"), Closed(0, 1, "motion")]
    assert tracker.is_open is False


def test_motion_inside_post_roll_keeps_event_open():
    tracker = EventTracker(post_roll=5, max_event=60)
    samples = [(0, True), (4, False), (6, True), (10, False)]
    assert feed(tracker, samples) == [Opened(0, "motion")]
    assert tracker.is_open is True


def test_long_event_is_cut_off_and_a_new_one_starts():
    tracker = EventTracker(post_roll=5, max_event=10)
    samples = [(t, True) for t in range(0, 14)]
    signals = feed(tracker, samples)
    assert signals == [Opened(0, "motion"), Closed(0, 10, "motion"), Opened(11, "motion")]


def test_flush_closes_an_open_event():
    tracker = EventTracker(post_roll=5, max_event=60)
    feed(tracker, [(0, True), (2, True)])
    assert tracker.flush() == Closed(0, 2, "motion")
    assert tracker.flush() is None


def test_reason_is_carried_through():
    tracker = EventTracker(post_roll=1, max_event=60)
    assert tracker.update(0, True, reason="person") == Opened(0, "person")
    assert tracker.update(2, False) == Closed(0, 0, "person")
