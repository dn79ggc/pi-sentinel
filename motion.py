import cv2


class MotionDetector:
    """Flags frames that differ from a slowly updating background."""

    def __init__(
        self,
        min_fraction=0.01,
        pixel_threshold=25,
        alpha=0.05,
        work_width=320,
        warmup_frames=15,
    ):
        self.min_fraction = min_fraction
        self.pixel_threshold = pixel_threshold
        self.alpha = alpha
        self.work_width = work_width
        self.warmup_frames = warmup_frames
        self._background = None
        self._seen = 0

    def _prepare(self, frame):
        height, width = frame.shape[:2]
        scale = self.work_width / width
        small = cv2.resize(frame, (self.work_width, max(1, int(height * scale))))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (21, 21), 0)

    def update(self, frame):
        gray = self._prepare(frame)
        if self._background is None:
            self._background = gray.astype("float32")
            self._seen = 1
            return False

        diff = cv2.absdiff(gray, cv2.convertScaleAbs(self._background))
        cv2.accumulateWeighted(gray, self._background, self.alpha)
        self._seen += 1
        if self._seen <= self.warmup_frames:
            return False

        _, mask = cv2.threshold(diff, self.pixel_threshold, 255, cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, None, iterations=2)
        return cv2.countNonZero(mask) / mask.size >= self.min_fraction
