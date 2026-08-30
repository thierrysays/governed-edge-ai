"""
Tests for perception/uno_q_service.py.

Uses SyntheticFrameSource and a mock TCP server to verify the perception
service processes frames and forwards DetectionResult objects correctly.
All tests are hardware-free.
"""

import socket
from unittest.mock import MagicMock, patch

import numpy as np

from perception.backends import NullPipeline, StubObjectDetector
from perception.base import DetectionResult
from perception.capture import SyntheticFrameSource
from perception.uno_q_service import MultiBackendPipeline, PerceptionService


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# MultiBackendPipeline tests
# ---------------------------------------------------------------------------

class TestMultiBackendPipeline:
    def _frame(self) -> np.ndarray:
        return np.zeros((48, 64, 3), dtype=np.uint8)

    def test_single_backend_results_returned(self):
        pipeline = MultiBackendPipeline([StubObjectDetector(confidence=0.91)])
        results = pipeline.run(self._frame())
        assert len(results) == 1
        assert results[0].label == "person"

    def test_null_backend_returns_empty(self):
        pipeline = MultiBackendPipeline([NullPipeline()])
        assert pipeline.run(self._frame()) == []

    def test_multiple_backends_merged(self):
        from perception.backends import StubGestureRecognizer
        pipeline = MultiBackendPipeline([
            StubObjectDetector(confidence=0.85),
            StubGestureRecognizer(confidence=0.92),
        ])
        results = pipeline.run(self._frame())
        assert len(results) == 2
        labels = {r.label for r in results}
        assert "person" in labels
        assert "stop" in labels

    def test_results_sorted_by_confidence_desc(self):
        from perception.backends import StubGestureRecognizer
        pipeline = MultiBackendPipeline([
            StubObjectDetector(confidence=0.72),
            StubGestureRecognizer(confidence=0.95),
        ])
        results = pipeline.run(self._frame())
        confs = [r.confidence for r in results]
        assert confs == sorted(confs, reverse=True)

    def test_backend_exception_is_swallowed(self):
        bad = NullPipeline()
        bad.run = MagicMock(side_effect=RuntimeError("camera error"))  # type: ignore
        pipeline = MultiBackendPipeline([bad, StubObjectDetector()])
        results = pipeline.run(self._frame())
        # StubObjectDetector should still return its result
        assert any(r.label == "person" for r in results)


# ---------------------------------------------------------------------------
# PerceptionService integration tests
# ---------------------------------------------------------------------------

class _RecordingClient:
    """Captures sent results for assertions."""

    def __init__(self):
        self.batches: list[list[DetectionResult]] = []
        self.closed = False

    def send(self, results: list[DetectionResult]) -> None:
        self.batches.append(list(results))

    def close(self) -> None:
        self.closed = True


class TestPerceptionService:
    def _make_service(self, backends, frame_count=3):
        source = SyntheticFrameSource(fps=1000.0, frame_count=frame_count)
        pipeline = MultiBackendPipeline(backends)
        client = _RecordingClient()
        service = PerceptionService(
            source=source, pipeline=pipeline, client=client, max_frames=frame_count
        )
        return service, client

    def test_sends_one_batch_per_frame_with_detections(self):
        service, client = self._make_service([StubObjectDetector(0.91)], frame_count=3)
        service.run()
        assert len(client.batches) == 3

    def test_null_pipeline_sends_nothing(self):
        service, client = self._make_service([NullPipeline()], frame_count=5)
        service.run()
        assert client.batches == []

    def test_client_closed_after_run(self):
        service, client = self._make_service([NullPipeline()], frame_count=1)
        service.run()
        assert client.closed

    def test_stop_terminates_loop(self):
        source = SyntheticFrameSource(fps=1000.0, frame_count=1000)
        pipeline = MultiBackendPipeline([NullPipeline()])
        client = _RecordingClient()
        service = PerceptionService(source=source, pipeline=pipeline, client=client)
        service.stop()
        service.run()
        assert client.batches == []

    def test_client_send_error_does_not_crash_service(self):
        source = SyntheticFrameSource(fps=1000.0, frame_count=2)
        pipeline = MultiBackendPipeline([StubObjectDetector(0.91)])
        client = _RecordingClient()
        client.send = MagicMock(side_effect=OSError("network gone"))  # type: ignore
        service = PerceptionService(
            source=source, pipeline=pipeline, client=client, max_frames=2
        )
        service.run()  # must not raise

    def test_main_entrypoint_synthetic_mode(self):
        from perception.uno_q_service import main
        # --source synthetic, 0 max_frames via patching PerceptionService
        with patch("perception.uno_q_service.PerceptionService") as MockService:
            instance = MockService.return_value
            instance.run = MagicMock()
            result = main(["--source", "synthetic", "--host", "127.0.0.1"])
        assert result == 0

    def test_main_logs_when_sentry_enabled(self):
        from perception.uno_q_service import main
        with (
            patch("perception.uno_q_service.init_sentry", return_value=True),
            patch("perception.uno_q_service.PerceptionService") as MockService,
        ):
            instance = MockService.return_value
            instance.run = MagicMock()
            result = main(["--source", "synthetic", "--host", "127.0.0.1"])
        assert result == 0
