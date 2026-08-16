from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from .frame import CapturedFrame


class StreamUnavailable(RuntimeError):
    pass


class OpenCVFrameSource:
    """Decode a stream without ever persisting raw or encoded frames."""

    def __init__(
        self,
        stream_url: str,
        *,
        width: int = 640,
        height: int = 360,
        jpeg_quality: int = 75,
        consecutive_read_failures: int = 5,
    ) -> None:
        if not stream_url:
            raise ValueError("stream_url is required")
        if width < 1 or height < 1:
            raise ValueError("target dimensions must be positive")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        self._stream_url = stream_url
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.consecutive_read_failures = consecutive_read_failures

    async def frames(self) -> AsyncIterator[CapturedFrame]:
        cv2 = self._import_cv2()
        capture = await asyncio.to_thread(cv2.VideoCapture, self._stream_url)
        if not capture or not capture.isOpened():
            if capture:
                capture.release()
            raise StreamUnavailable("unable to open configured stream")

        sequence = 0
        failures = 0
        loop = asyncio.get_running_loop()
        try:
            while True:
                ok, image = await asyncio.to_thread(capture.read)
                if not ok or image is None:
                    failures += 1
                    if failures >= self.consecutive_read_failures:
                        raise StreamUnavailable("configured stream stopped producing frames")
                    await asyncio.sleep(0.05)
                    continue
                failures = 0
                resized = cv2.resize(
                    image,
                    (self.width, self.height),
                    interpolation=cv2.INTER_AREA,
                )
                encoded_ok, encoded = cv2.imencode(
                    ".jpg",
                    resized,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
                )
                if not encoded_ok:
                    continue
                fingerprint = self._difference_hash(resized, cv2)
                yield CapturedFrame(
                    sequence=sequence,
                    monotonic_ts=loop.time(),
                    captured_at=datetime.now(UTC),
                    jpeg=encoded.tobytes(),
                    fingerprint=fingerprint,
                    width=self.width,
                    height=self.height,
                )
                sequence += 1
        finally:
            await asyncio.to_thread(capture.release)

    @staticmethod
    def _difference_hash(image: Any, cv2: Any) -> int:
        # Hash each BGR channel independently. A grayscale dHash collapsed the
        # moving color bars from FFmpeg's testsrc into one value, incorrectly
        # treating a dynamic stream as frozen. The 192-bit color dHash retains
        # perceptual tolerance while detecting chromatic motion.
        small = cv2.resize(image, (9, 8), interpolation=cv2.INTER_AREA)
        comparisons = (small[:, 1:, :] > small[:, :-1, :]).transpose(2, 0, 1)
        fingerprint = 0
        for index, value in enumerate(comparisons.flat):
            if bool(value):
                fingerprint |= 1 << index
        return fingerprint

    @staticmethod
    def _import_cv2() -> Any:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised by container smoke test
            raise RuntimeError("opencv-python-headless is required for video capture") from exc
        return cv2

    def __repr__(self) -> str:
        return (
            "OpenCVFrameSource(stream_url=<redacted>, "
            f"width={self.width}, height={self.height}, jpeg_quality={self.jpeg_quality})"
        )
