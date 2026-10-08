import pytest

from outbox import Outbox
from scheduler import Dispatcher, Pacer


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def outbox():
    box = Outbox(":memory:")
    yield box
    box.close()


def build(outbox, clock, handlers, channels=("discord", "gemini"), gemini_gap=30, discord_gap=3, **kwargs):
    pacers = {
        "discord": Pacer(outbox, "discord", discord_gap),
        "gemini": Pacer(outbox, "gemini", gemini_gap),
    }
    return Dispatcher(outbox, channels, handlers, pacers, clock=clock, backoff=10, max_retries=5, **kwargs)


def run_until(dispatcher, clock, end):
    while clock.now <= end:
        while dispatcher.step():
            pass
        clock.now += 1


def recorder(log, clock):
    return lambda job, event: log.append((job["kind"], job["event_id"], clock.now))


def test_pacer_formula():
    box = Outbox(":memory:")
    pacer = Pacer(box, "gemini", 30)
    assert pacer.ready_at(13) == 13
    pacer.record(13)
    assert pacer.ready_at(25) == 43
    assert pacer.ready_at(60) == 60
    pacer.push_back(100)
    assert pacer.ready_at(50) == 100
    pacer.push_back(90)
    assert pacer.ready_at(50) == 100


def test_back_to_back_events_get_slots_13_43_73(outbox):
    clock = Clock()
    log = []
    for event_id, ready in [(1, 13), (2, 25), (3, 38)]:
        outbox.add_job("analyze", ready, event_id=event_id)
    dispatcher = build(outbox, clock, {"analyze": recorder(log, clock)})
    run_until(dispatcher, clock, 120)
    assert [(event, at) for _, event, at in log] == [(1, 13), (2, 43), (3, 73)]


def test_alerts_are_spaced_by_the_alert_gap(outbox):
    clock = Clock()
    log = []
    for event_id in (1, 2, 3):
        outbox.add_job("alert", 0, event_id=event_id)
    dispatcher = build(outbox, clock, {"alert": recorder(log, clock)})
    run_until(dispatcher, clock, 20)
    assert [at for _, _, at in log] == [0, 3, 6]


def test_alert_is_not_held_up_by_a_waiting_analysis(outbox):
    clock = Clock()
    log = []
    handlers = {"alert": recorder(log, clock), "analyze": recorder(log, clock)}
    outbox.set_pace("gemini", 0)
    outbox.add_job("analyze", 1, event_id=1)
    outbox.add_job("alert", 5, event_id=2)
    dispatcher = build(outbox, clock, handlers)
    run_until(dispatcher, clock, 40)
    assert log == [("alert", 2, 5), ("analyze", 1, 30)]


def test_dispatcher_only_serves_its_channels(outbox):
    clock = Clock()
    log = []
    outbox.add_job("analyze", 0, event_id=1)
    outbox.add_job("notice", 0)
    dispatcher = build(outbox, clock, {"analyze": recorder(log, clock), "notice": recorder(log, clock)}, channels=("discord",))
    run_until(dispatcher, clock, 10)
    assert [kind for kind, _, _ in log] == ["notice"]


def test_failed_job_is_retried_with_growing_delay(outbox):
    clock = Clock()
    attempts = []

    def flaky(job, event):
        attempts.append(clock.now)
        if len(attempts) < 3:
            raise RuntimeError("network down")

    outbox.add_job("alert", 0)
    dispatcher = build(outbox, clock, {"alert": flaky})
    run_until(dispatcher, clock, 60)
    assert attempts == [0, 10, 30]
    assert outbox.count_pending("alert") == 0


def test_job_is_abandoned_after_max_retries_and_hook_runs(outbox):
    clock = Clock()
    seen = []

    def always_fails(job, event):
        raise RuntimeError("boom")

    outbox.add_job("analyze", 0, event_id=1)
    outbox.create_event(0, "motion")
    dispatcher = build(
        outbox, clock, {"analyze": always_fails}, gemini_gap=1,
        on_give_up={"analyze": lambda job, event, exc: seen.append((job["attempts"], type(exc).__name__))},
    )
    run_until(dispatcher, clock, 1000)
    assert seen == [(4, "RuntimeError")]
    assert outbox.count_pending("analyze") == 0


def test_rate_limit_pushes_the_whole_channel_back(outbox):
    clock = Clock()
    log = []

    class Limited(Exception):
        retry_after = 50

    state = {"first": True}

    def handler(job, event):
        if state["first"]:
            state["first"] = False
            raise Limited()
        log.append((job["event_id"], clock.now))

    outbox.add_job("analyze", 0, event_id=1)
    outbox.add_job("analyze", 1, event_id=2)
    dispatcher = build(outbox, clock, {"analyze": handler})
    run_until(dispatcher, clock, 200)
    assert log[0][1] >= 50
    assert {event for event, _ in log} == {1, 2}


def test_drop_policy_drops_work_that_would_have_to_wait(outbox):
    clock = Clock()
    log = []
    dropped = []
    outbox.set_pace("gemini", 0)
    outbox.add_job("analyze", 0, event_id=1)
    dispatcher = build(
        outbox, clock, {"analyze": recorder(log, clock)}, policy="drop",
        on_drop={"analyze": lambda job, event: dropped.append(job["event_id"])},
    )
    clock.now = 5
    assert dispatcher.step() is False
    clock.now = 30
    assert dispatcher.step() is True
    assert dropped == [1]
    assert log == []


def test_drop_policy_runs_work_that_is_on_time(outbox):
    clock = Clock(100)
    log = []
    outbox.add_job("analyze", 100, event_id=1)
    dispatcher = build(outbox, clock, {"analyze": recorder(log, clock)}, policy="drop", on_drop={"analyze": lambda j, e: None})
    dispatcher.step()
    assert log == [("analyze", 1, 100)]
