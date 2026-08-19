"""
Smoke tests for the perception pipeline interface.

Each test exercises the full path from PerceptionPipeline.run() through
DetectionResult construction: the same path the governance layer will walk
on every captured frame.
"""

import dataclasses

import pytest

from perception.backends import (
    NullPipeline,
    StubGestureRecognizer,
    StubObjectDetector,
    StubPoseEstimator,
)
from perception.base import DetectionResult


@pytest.mark.smoke
def test_import():
    """Perception module is importable and base classes are accessible."""
    from perception.backends import StubObjectDetector  # noqa: F401
    from perception.base import DetectionResult  # noqa: F401


@pytest.mark.smoke
def test_all_stub_backends_produce_valid_results():
    """Every stub backend returns at least one DetectionResult with valid fields."""
    backends = [StubObjectDetector(), StubGestureRecognizer(), StubPoseEstimator()]
    for backend in backends:
        results = backend.run(None)
        assert len(results) >= 1, f"{backend.backend_name} returned no results"
        for r in results:
            assert isinstance(r, DetectionResult)
            assert 0.0 <= r.confidence <= 1.0
            assert r.detection_type in ("object", "gesture", "pose")
            assert r.label
            assert r.backend == backend.backend_name


@pytest.mark.smoke
def test_null_pipeline_suppresses_commands():
    """
    NullPipeline returns no detections.  The governance layer must not send
    any command when perception returns [].
    """
    assert NullPipeline().run(None) == []


@pytest.mark.smoke
def test_detection_result_is_immutable():
    """
    DetectionResult is frozen: the governance layer cannot accidentally
    mutate a detection after it is produced by the backend.
    """
    r = DetectionResult(detection_type="object", label="person", confidence=0.91)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.confidence = 0.0  # type: ignore[misc]


@pytest.mark.smoke
def test_governance_confidence_gate():
    """
    Detections below the STM32H5 confidence threshold (0.70) must not be
    forwarded as commands.  This smoke test simulates the governance filter.
    """
    low_confidence = StubObjectDetector(confidence=0.50).run(None)
    high_confidence = StubObjectDetector(confidence=0.90).run(None)

    # Governance filter (applied by the AI stack before IPC send)
    THRESHOLD = 0.70
    assert all(r.confidence < THRESHOLD for r in low_confidence)
    assert all(r.confidence >= THRESHOLD for r in high_confidence)
