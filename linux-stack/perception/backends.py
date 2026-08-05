"""
Stub perception backends.

Each class is a complete, importable implementation that returns realistic
dummy detections. They exercise the full PerceptionPipeline interface and
can be used in offline development and CI without any model weights or
hardware (camera, NPU) present.

Production backends (YOLO-X on the NPU, MediaPipe Hands, PoseNet) will
replace these stubs once the VENTUNO Q GPIO/MIPI-CSI pinout is confirmed
and the NPU SDK is available. The interface contract (DetectionResult,
PerceptionPipeline.run) is what matters now.
"""

from __future__ import annotations

from perception.base import DetectionResult, PerceptionPipeline


class StubObjectDetector(PerceptionPipeline):
    """
    Stub for YOLO-X / MobileNet-SSD object detection.

    Returns a single "person" detection with configurable confidence.
    Simulates the output expected from the NPU-accelerated object detector
    watching the robotic arm's workspace.
    """

    backend_name = "stub-object"

    def __init__(self, confidence: float = 0.91) -> None:
        self._confidence = confidence

    def run(self, frame: object) -> list[DetectionResult]:
        return [
            DetectionResult(
                detection_type="object",
                label="person",
                confidence=self._confidence,
                bounding_box=(10, 10, 100, 200),
                backend=self.backend_name,
            )
        ]


class StubGestureRecognizer(PerceptionPipeline):
    """
    Stub for MediaPipe Hands gesture recognition.

    Returns a "stop" gesture detection. In production this backend will call
    the MediaPipe Hands graph on CPU (the gesture model is small enough not
    to need the NPU) and classify the dominant hand pose.
    """

    backend_name = "stub-gesture"

    def __init__(self, confidence: float = 0.88) -> None:
        self._confidence = confidence

    def run(self, frame: object) -> list[DetectionResult]:
        return [
            DetectionResult(
                detection_type="gesture",
                label="stop",
                confidence=self._confidence,
                backend=self.backend_name,
            )
        ]


class StubPoseEstimator(PerceptionPipeline):
    """
    Stub for PoseNet / MoveNet proximity-breach detection.

    Returns a "proximity_breach" pose detection. In production this backend
    runs MoveNet Lightning on the NPU and classifies operator skeleton pose
    relative to the arm's danger zone.
    """

    backend_name = "stub-pose"

    def __init__(self, confidence: float = 0.76) -> None:
        self._confidence = confidence

    def run(self, frame: object) -> list[DetectionResult]:
        return [
            DetectionResult(
                detection_type="pose",
                label="proximity_breach",
                confidence=self._confidence,
                backend=self.backend_name,
            )
        ]


class NullPipeline(PerceptionPipeline):
    """
    Returns no detections. Useful for testing the governance layer's
    behaviour when perception produces nothing (no command should be sent).
    """

    backend_name = "null"

    def run(self, frame: object) -> list[DetectionResult]:
        return []
