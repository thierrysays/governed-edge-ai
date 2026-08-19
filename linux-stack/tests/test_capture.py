"""
Tests for perception/capture.py.

All tests are hardware-free: they use SyntheticFrameSource only.
V4L2FrameSource and FileFrameSource require physical devices or video files;
they are tested by inspection (constructor checks) rather than live capture.
"""

import numpy as np
import pytest

from perception.capture import FrameSource, SyntheticFrameSource


class TestSyntheticFrameSource:
    def test_yields_ndarray(self):
        src = SyntheticFrameSource(width=64, height=48, fps=100.0, frame_count=1)
        frames = list(src.frames())
        assert len(frames) == 1
        assert isinstance(frames[0], np.ndarray)

    def test_frame_shape(self):
        src = SyntheticFrameSource(width=80, height=60, fps=100.0, frame_count=1)
        frame = next(iter(src.frames()))
        assert frame.shape == (60, 80, 3)

    def test_frame_dtype(self):
        src = SyntheticFrameSource(fps=100.0, frame_count=1)
        frame = next(iter(src.frames()))
        assert frame.dtype == np.uint8

    def test_frame_color(self):
        color = (10, 20, 30)
        src = SyntheticFrameSource(fps=100.0, frame_count=1, color=color)
        frame = next(iter(src.frames()))
        assert tuple(frame[0, 0]) == color

    def test_frame_count_limits(self):
        src = SyntheticFrameSource(fps=1000.0, frame_count=5)
        frames = list(src.frames())
        assert len(frames) == 5

    def test_frame_source_is_abc(self):
        assert issubclass(SyntheticFrameSource, FrameSource)

    def test_close_is_noop(self):
        src = SyntheticFrameSource(fps=100.0, frame_count=0)
        src.close()  # must not raise

    def test_default_color_is_grey(self):
        src = SyntheticFrameSource(fps=100.0, frame_count=1)
        frame = next(iter(src.frames()))
        pixel = tuple(frame[0, 0])
        assert pixel == (128, 128, 128)

    def test_v4l2_import_present(self):
        from perception.capture import V4L2FrameSource
        assert V4L2FrameSource is not None

    def test_file_frame_source_import_present(self):
        from perception.capture import FileFrameSource
        assert FileFrameSource is not None

    def test_v4l2_raises_without_device(self):
        from perception.capture import V4L2FrameSource
        with pytest.raises((RuntimeError, Exception)):  # noqa: B017
            src = V4L2FrameSource(device_index=99)
            next(src.frames())


class TestSyntheticFrameSourceInfinite:
    def test_can_iterate_multiple_frames(self):
        src = SyntheticFrameSource(fps=1000.0, frame_count=20)
        count = sum(1 for _ in src.frames())
        assert count == 20
