import pytest

from outbox import Outbox


@pytest.fixture
def outbox():
    box = Outbox(":memory:")
    yield box
    box.close()


def test_event_roundtrip(outbox):
    event_id = outbox.create_event(100.0, "motion")
    outbox.set_clip(event_id, "data/clips/event-0001/clip.mp4", ["a.jpg", "b.jpg"], 14.2)
    outbox.close_event(event_id, 108.0)
    event = outbox.get_event(event_id)
    assert event["status"] == "closed"
    assert event["keyframes"] == ["a.jpg", "b.jpg"]
    assert event["clip_seconds"] == 14.2
    assert outbox.get_event(999) is None


def test_jobs_run_in_due_order_then_id(outbox):
    first = outbox.add_job("alert", 5.0)
    second = outbox.add_job("alert", 2.0)
    third = outbox.add_job("alert", 2.0)
    assert [job["id"] for job in outbox.runnable_jobs()] == [second, third, first]


def test_analysis_waits_for_its_alert(outbox):
    event_id = outbox.create_event(0.0, "motion")
    alert = outbox.add_job("alert", 0.0, event_id=event_id)
    analyze = outbox.add_job("analyze", 1.0, event_id=event_id)
    assert [job["id"] for job in outbox.runnable_jobs()] == [alert]
    outbox.update_job(alert, state="done")
    assert [job["id"] for job in outbox.runnable_jobs()] == [analyze]


def test_failed_alert_does_not_block_the_report(outbox):
    event_id = outbox.create_event(0.0, "motion")
    alert = outbox.add_job("alert", 0.0, event_id=event_id)
    finish = outbox.add_job("finish", 1.0, event_id=event_id)
    outbox.update_job(alert, state="failed")
    assert [job["id"] for job in outbox.runnable_jobs()] == [finish]


def test_other_events_are_not_blocked(outbox):
    one = outbox.create_event(0.0, "motion")
    two = outbox.create_event(1.0, "motion")
    outbox.add_job("alert", 5.0, event_id=one)
    analyze_two = outbox.add_job("analyze", 1.0, event_id=two)
    assert analyze_two in [job["id"] for job in outbox.runnable_jobs()]


def test_count_pending_by_kind(outbox):
    outbox.add_job("analyze", 0.0)
    done = outbox.add_job("analyze", 0.0)
    outbox.add_job("alert", 0.0)
    outbox.update_job(done, state="done")
    assert outbox.count_pending("analyze") == 1


def test_pace_and_meta_persist_values(outbox):
    assert outbox.get_pace("gemini") is None
    outbox.set_pace("gemini", 123.5)
    outbox.set_pace("gemini", 130.0)
    assert outbox.get_pace("gemini") == 130.0
    assert outbox.get_meta("missing", "fallback") == "fallback"


def test_daily_call_counter_is_per_day(outbox):
    outbox.add_call("2026-10-08")
    outbox.add_call("2026-10-08")
    outbox.add_call("2026-10-09")
    assert outbox.calls_on("2026-10-08") == 2
    assert outbox.calls_on("2026-10-09") == 1
    assert outbox.calls_on("2026-10-10") == 0


def test_recover_reports_events_cut_short_by_a_restart(outbox):
    open_id = outbox.create_event(0.0, "motion")
    unfinished_id = outbox.create_event(1.0, "motion")
    outbox.close_event(unfinished_id, 5.0)
    queued_id = outbox.create_event(2.0, "motion")
    outbox.close_event(queued_id, 6.0)
    outbox.add_job("analyze", 9.0, event_id=queued_id)
    assert outbox.recover(now=50.0) == [open_id, unfinished_id]
    event = outbox.get_event(open_id)
    assert event["status"] == "interrupted"
    assert "restarted" in event["note"]
    assert outbox.count_pending("finish") == 2
    assert outbox.recover(now=60.0) == []


def test_data_survives_reopening_the_file(tmp_path):
    path = tmp_path / "sentinel.db"
    box = Outbox(path)
    event_id = box.create_event(1.0, "motion")
    box.add_job("alert", 1.0, event_id=event_id)
    box.close()
    again = Outbox(path)
    assert again.get_event(event_id)["reason"] == "motion"
    assert len(again.runnable_jobs()) == 1
    again.close()
