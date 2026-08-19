"""
Camera capture abstraction for the Arduino UNO Q 4GB.

The UNO Q 4GB exposes its dual 13 MP ISP channels through the V4L2 kernel
subsystem as /dev/video0 and /dev/video1. This module wraps OpenCV's
VideoCapture to provide a frame iterator that works identically on real
hardware (V4L2 device) and in offline development (synthetic frames, a
local video file, or a directory of images).

All frames are returned as BGR uint8 numpy arrays (H x W x 3), which is the
format expected by PerceptionPipeline.run().
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator

import numpy as np


class FrameSource(ABC):
    """Abstract base for all camera / frame sources."""

    @abstractmethod
    def frames(self) -> Iterator[np.ndarray]:
        """Yield BGR uint8 frames indefinitely (or until the source is exhausted)."""

    def close(self) -> None:  # noqa: B027 -- intentional no-op default; subclasses override
        pass


class V4L2FrameSource(FrameSource):  # pragma: no cover
    """
    Live camera capture via V4L2 / OpenCV.

    Uses /dev/video0 by default (first ISP channel on UNO Q 4GB).
    Set device_index=1 for the second ISP channel.
    """

    def __init__(
        self,
        device_index: int = 0,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
    ) -> None:
        import cv2
        self._cap = cv2.VideoCapture(device_index, cv2.CAP_V4L2)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, fps)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Cannot open /dev/video{device_index}. "
                "Is the camera module connected and recognised by V4L2?"
            )

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            ret, frame = self._cap.read()
            if not ret:
                break
            yield frame

    def close(self) -> None:
        if self._cap.isOpened():
            self._cap.release()


class SyntheticFrameSource(FrameSource):
    """
    Hardware-free frame source for offline development and CI.

    Yields solid-colour BGR frames at the specified resolution and rate.
    Useful for exercising the full pipeline (capture -> inference -> IPC)
    without a physical camera.
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        fps: float = 10.0,
        frame_count: int | None = None,
        color: tuple[int, int, int] = (128, 128, 128),
    ) -> None:
        self._width = width
        self._height = height
        self._interval = 1.0 / fps
        self._frame_count = frame_count
        self._color = color

    def frames(self) -> Iterator[np.ndarray]:
        count = 0
        while self._frame_count is None or count < self._frame_count:
            t0 = time.monotonic()
            frame = np.full((self._height, self._width, 3), self._color, dtype=np.uint8)
            yield frame
            count += 1
            elapsed = time.monotonic() - t0
            sleep_s = self._interval - elapsed
            if sleep_s > 0:
                time.sleep(sleep_s)


class FileFrameSource(FrameSource):  # pragma: no cover
    """
    Frame source backed by a video file or image directory (dev/test use).
    """

    def __init__(self, path: str, loop: bool = False) -> None:
        import cv2
        self._cap = cv2.VideoCapture(path)
        self._loop = loop
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video source: {path!r}")

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            ret, frame = self._cap.read()
            if not ret:
                if self._loop:
                    self._cap.set(0, 0)  # rewind (CAP_PROP_POS_FRAMES=0)
                    continue
                break
            yield frame

    def close(self) -> None:
        if self._cap.isOpened():
            self._cap.release()
