from buffer import FrameBuffer


def test_window_returns_frames_in_range_in_order():
    buf = FrameBuffer(max_seconds=100)
    for t in range(10):
        buf.add(float(t), bytes([t]))
    assert [ts for ts, _ in buf.window(3, 6)] == [3.0, 4.0, 5.0, 6.0]


def test_old_frames_are_dropped():
    buf = FrameBuffer(max_seconds=5)
    for t in range(20):
        buf.add(float(t), b"x")
    assert buf.window(0, 100)[0][0] == 14.0
    assert len(buf) == 6


def test_window_outside_data_is_empty():
    buf = FrameBuffer(max_seconds=5)
    buf.add(1.0, b"x")
    assert buf.window(50, 60) == []
