import json

import pytest

import notifier

URL = "https://discord.example/api/webhooks/1/token"


class FakeResponse:
    def __init__(self, status=200, body=None, text="", headers=None, has_json=True):
        self.status_code = status
        self._body = body if body is not None else {}
        self._has_json = has_json
        self.text = text
        self.headers = headers or {}

    def json(self):
        if not self._has_json:
            raise ValueError("no json")
        return self._body


class Sent(list):
    """Recorded requests. Set `state["response"]` to change what the fake server answers."""

    def __init__(self):
        super().__init__()
        self.state = {"response": FakeResponse(body={"id": "555"})}


@pytest.fixture
def sent(monkeypatch):
    calls = Sent()

    def fake(method):
        def handler(url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            return calls.state["response"]

        return handler

    monkeypatch.setattr(notifier.requests, "post", fake("POST"))
    monkeypatch.setattr(notifier.requests, "patch", fake("PATCH"))
    return calls


def test_post_returns_message_id_and_waits(sent):
    assert notifier.post_message(URL, "hello") == "555"
    call = sent[0]
    assert call["params"] == {"wait": "true"}
    assert call["data"] == {"content": "hello"}
    assert call["files"] is None


def test_post_truncates_long_text_and_names_files(sent):
    notifier.post_message(URL, "x" * 5000, [("a.mp4", b"1", "video/mp4"), ("b.jpg", b"2", "image/jpeg")])
    call = sent[0]
    assert len(call["data"]["content"]) == notifier.CONTENT_LIMIT
    assert list(call["files"]) == ["files[0]", "files[1]"]
    assert call["files"]["files[0]"][0] == "a.mp4"


def test_edit_with_attachment_sends_payload_json(sent):
    notifier.edit_message(URL + "/", "555", "done", [("clip.mp4", b"data", "video/mp4")])
    call = sent[0]
    assert call["method"] == "PATCH"
    assert call["url"] == f"{URL}/messages/555"
    payload = json.loads(call["data"]["payload_json"])
    assert payload == {"content": "done", "attachments": [{"id": 0, "filename": "clip.mp4"}]}
    assert "files[0]" in call["files"]


def test_edit_without_attachment_sends_plain_json(sent):
    notifier.edit_message(URL, "555", "done")
    assert sent[0]["json"] == {"content": "done"}


def test_rate_limit_carries_retry_after(sent):
    sent.state["response"] = FakeResponse(429, {"retry_after": 2.5})
    with pytest.raises(notifier.RateLimited) as caught:
        notifier.post_message(URL, "hello")
    assert caught.value.retry_after == 2.5


def test_rate_limit_without_json_uses_header(sent):
    sent.state["response"] = FakeResponse(429, headers={"Retry-After": "7"}, has_json=False)
    with pytest.raises(notifier.RateLimited) as caught:
        notifier.edit_message(URL, "555", "x")
    assert caught.value.retry_after == 7.0


def test_other_errors_raise_discord_error_but_not_rate_limit(sent):
    sent.state["response"] = FakeResponse(404, text="Unknown Message")
    with pytest.raises(notifier.DiscordError) as caught:
        notifier.edit_message(URL, "555", "x")
    assert not isinstance(caught.value, notifier.RateLimited)


def test_alert_text_matches_the_approved_format():
    text = notifier.alert_text(12, 1_700_000_000, "motion")
    assert text == "Event 12 | detected <t:1700000000:T> | motion\nClip and analysis to follow."


def test_report_text_matches_the_approved_format():
    text = notifier.report_text(12, 1_700_000_000, 1_700_000_065, 14.2, "A person walks in.")
    assert text == (
        "Event 12 | detected <t:1700000000:T> | 14 s long | posted <t:1700000065:T> (65 s after)\n"
        "A person walks in."
    )


def test_report_text_without_length_and_with_long_wait():
    text = notifier.report_text(3, 0, 600, None, "body", discord_time=False)
    assert "s long" not in text
    assert "(10 min after)" in text
    assert text.endswith("\nbody")


def test_report_text_is_capped():
    text = notifier.report_text(1, 0, 5, 3, "y" * 5000)
    assert len(text) == notifier.CONTENT_LIMIT
    assert text.startswith("Event 1 | ")


def test_plain_timestamps_when_discord_markup_is_off():
    text = notifier.alert_text(1, 0, "motion", discord_time=False)
    assert "<t:" not in text
    assert ":" in text.split("|")[1]


def test_backlog_text():
    assert notifier.backlog_text(7, 3.5) == "Backlog: 7 analyses waiting, about 3.5 min behind."
