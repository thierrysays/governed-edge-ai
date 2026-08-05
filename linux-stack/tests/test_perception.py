"""
Unit tests for linux-stack/perception/base.py and perception/backends.py.

No camera, no model weights, no hardware — all tests use the stub backends
and a dummy frame (None or a small numpy-like object).
"""

import dataclasses

import pytest

from perception.backends import (
    NullPipeline,
    StubGestureRecognizer,
    StubObjectDetector,
    StubPoseEstimator,
)
from perception.base import _CONFIDENCE_MAX, DetectionResult, PerceptionPipeline

# ---------------------------------------------------------------------------
# DetectionResult construction and validation
# ---------------------------------------------------------------------------

class TestDetectionResult:
    def test_basic_construction(self):
        r = DetectionResult(detection_type="object", label="person", confidence=0.91)
        assert r.label == "person"
        assert r.detection_type == "object"
        assert r.confidence == pytest.approx(0.91)

    def test_is_frozen(self):
        r = DetectionResult(detection_type="object", label="person", confidence=0.91)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.label = "cat"  # type: ignore[misc]

    def test_timestamp_us_auto_populated(self):
        r = DetectionResult(detection_type="gesture", label="stop", confidence=0.80)
        assert r.timestamp_us > 0

    def test_explicit_timestamp_preserved(self):
        r = DetectionResult(detection_type="pose", label="breach", confidence=0.70, timestamp_us=42)
        assert r.timestamp_us == 42

    def test_bounding_box_default_none(self):
        r = DetectionResult(detection_type="object", label="person", confidence=0.91)
        assert r.bounding_box is None

    def test_bounding_box_set(self):
        r = DetectionResult(
            detection_type="object", label="person", confidence=0.91,
            bounding_box=(5, 10, 80, 120),
        )
        assert r.bounding_box == (5, 10, 80, 120)

    def test_backend_default_empty(self):
        r = DetectionResult(detection_type="object", label="person", confidence=0.91)
        assert r.backend == ""

    def test_confidence_clamped_at_max(self):
        r = DetectionResult(detection_type="object", label="x", confidence=1.0)
        assert r.confidence == _CONFIDENCE_MAX

    def test_confidence_zero_accepted(self):
        r = DetectionResult(detection_type="object", label="x", confidence=0.0)
        assert r.confidence == 0.0

    def test_confidence_negative_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            DetectionResult(detection_type="object", label="x", confidence=-0.01)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValueError, match="confidence"):
            DetectionResult(detection_type="object", label="x", confidence=1.0001)

    def test_all_detection_types_accepted(self):
        for dt in ("object", "gesture", "pose"):
            r = DetectionResult(detection_type=dt, label="x", confidence=0.5)  # type: ignore[arg-type]
            assert r.detection_type == dt

    def test_equality(self):
        r1 = DetectionResult(detection_type="object", label="person", confidence=0.9, timestamp_us=1)
        r2 = DetectionResult(detection_type="object", label="person", confidence=0.9, timestamp_us=1)
        assert r1 == r2

    def test_inequality_on_label(self):
        r1 = DetectionResult(detection_type="object", label="person", confidence=0.9, timestamp_us=1)
        r2 = DetectionResult(detection_type="object", label="car", confidence=0.9, timestamp_us=1)
        assert r1 != r2

    def test_passes_threshold_above(self):
        r = DetectionResult(detection_type="object", label="x", confidence=0.91)
        assert r.passes_threshold() is True

    def test_passes_threshold_below(self):
        r = DetectionResult(detection_type="object", label="x", confidence=0.50)
        assert r.passes_threshold() is False

    def test_passes_threshold_custom(self):
        r = DetectionResult(detection_type="object", label="x", confidence=0.80)
        assert r.passes_threshold(threshold=0.95) is False
        assert r.passes_threshold(threshold=0.75) is True


# ---------------------------------------------------------------------------
# PerceptionPipeline ABC
# ---------------------------------------------------------------------------

class TestPerceptionPipelineABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            PerceptionPipeline()  # type: ignore[abstract]

    def test_subclass_without_run_raises(self):
        class Incomplete(PerceptionPipeline):
            pass
        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_with_run_is_instantiable(self):
        class Minimal(PerceptionPipeline):
            def run(self, frame):
                return []
        p = Minimal()
        assert p.run(None) == []

    def test_warm_up_delegates_to_run(self):
        calls = []
        class Tracking(PerceptionPipeline):
            def run(self, frame):
                calls.append(frame)
                return []
        p = Tracking()
        p.warm_up("dummy-frame")
        assert calls == ["dummy-frame"]

    def test_stamp_populates_backend(self):
        class Named(PerceptionPipeline):
            backend_name = "my-backend"
            def run(self, frame):
                return []
        p = Named()
        r = DetectionResult(detection_type="object", label="x", confidence=0.5, timestamp_us=99)
        stamped = p._stamp(r)
        assert stamped.backend == "my-backend"
        assert stamped.timestamp_us == 99


# ---------------------------------------------------------------------------
# Stub backends
# ---------------------------------------------------------------------------

class TestStubObjectDetector:
    def test_returns_one_detection(self):
        assert len(StubObjectDetector().run(None)) == 1

    def test_detection_type_is_object(self):
        assert StubObjectDetector().run(None)[0].detection_type == "object"

    def test_label_is_person(self):
        assert StubObjectDetector().run(None)[0].label == "person"

    def test_default_confidence(self):
        assert StubObjectDetector().run(None)[0].confidence == pytest.approx(0.91)

    def test_custom_confidence(self):
        assert StubObjectDetector(confidence=0.55).run(None)[0].confidence == pytest.approx(0.55)

    def test_bounding_box_set(self):
        assert StubObjectDetector().run(None)[0].bounding_box is not None

    def test_backend_name(self):
        assert StubObjectDetector().run(None)[0].backend == "stub-object"

    def test_warm_up_does_not_raise(self):
        StubObjectDetector().warm_up(None)


class TestStubGestureRecognizer:
    def test_returns_one_detection(self):
        assert len(StubGestureRecognizer().run(None)) == 1

    def test_detection_type_is_gesture(self):
        assert StubGestureRecognizer().run(None)[0].detection_type == "gesture"

    def test_label_is_stop(self):
        assert StubGestureRecognizer().run(None)[0].label == "stop"

    def test_default_confidence(self):
        assert StubGestureRecognizer().run(None)[0].confidence == pytest.approx(0.88)

    def test_backend_name(self):
        assert StubGestureRecognizer().run(None)[0].backend == "stub-gesture"

    def test_no_bounding_box(self):
        assert StubGestureRecognizer().run(None)[0].bounding_box is None


class TestStubPoseEstimator:
    def test_returns_one_detection(self):
        assert len(StubPoseEstimator().run(None)) == 1

    def test_detection_type_is_pose(self):
        assert StubPoseEstimator().run(None)[0].detection_type == "pose"

    def test_label_is_proximity_breach(self):
        assert StubPoseEstimator().run(None)[0].label == "proximity_breach"

    def test_default_confidence(self):
        assert StubPoseEstimator().run(None)[0].confidence == pytest.approx(0.76)

    def test_backend_name(self):
        assert StubPoseEstimator().run(None)[0].backend == "stub-pose"


class TestNullPipeline:
    def test_returns_empty_list(self):
        assert NullPipeline().run(None) == []

    def test_backend_name(self):
        assert NullPipeline.backend_name == "null"

    def test_warm_up_does_not_raise(self):
        NullPipeline().warm_up(None)
