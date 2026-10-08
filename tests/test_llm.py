import llm


def test_build_message_carries_prompt_and_every_frame():
    message = llm.build_message([b"one", b"two", b"three"], reason="motion", seconds=14.2)
    kinds = [part["type"] for part in message.content]
    assert kinds == ["text", "image_url", "image_url", "image_url"]
    prompt = message.content[0]["text"]
    assert "3 frames" in prompt
    assert "motion" in prompt
    assert "14 seconds" in prompt
    assert message.content[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_build_message_without_length_omits_it():
    prompt = llm.build_message([b"one"]).content[0]["text"]
    assert "seconds long" not in prompt


def test_as_text_joins_block_lists():
    blocks = [{"type": "text", "text": "A person "}, {"type": "text", "text": "waves."}]
    assert llm._as_text(blocks) == "A person waves."


def test_quota_errors_are_recognized():
    assert llm.is_quota_error(RuntimeError("429 Too Many Requests"))
    assert llm.is_quota_error(RuntimeError("RESOURCE_EXHAUSTED: quota exceeded"))
    assert not llm.is_quota_error(RuntimeError("connection reset"))
