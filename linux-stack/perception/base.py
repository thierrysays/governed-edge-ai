"""
Perception pipeline interface definitions.

All inference backends (YOLO-X, MediaPipe, PoseNet, …) must implement
PerceptionPipeline and return DetectionResult values that the governance
layer translates into IPC CommandRequest frames.

Design constraints:
- DetectionResult is a frozen dataclass — the governance layer must not
  mutate a detection after it is produced.
- confidence is float in [0.0, 1.0]; the IPC layer encodes it as float32.
  Values above 0.9999 are clamped to prevent float32 overflow artefacts.
- PerceptionPipeline is an ABC; concrete backends are registered via
  subclassing, not monkey-patching.
- run() accepts raw BGR uint8 frames (numpy ndarray, H×W×3) — the format
  produced by OpenCV and most V4L2/MIPI-CSI capture pipelines.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

DetectionLabel = str
DetectionType = Literal["object", "gesture", "pose"]

_CONFIDENCE_MAX = 0.9999  # guard against float32 rounding above 1.0


@dataclass(frozen=True)
class DetectionResult:
    """
    One inference result from a perception backend.

    Attributes
    ----------
    detection_type:
        Category of detection; must match the audit_log CHECK constraint.
    label:
        Human-readable class name (e.g. "person", "stop", "proximity_breach").
    confidence:
        Model confidence in [0.0, 1.0]. Clamped to _CONFIDENCE_MAX on creation
        so float32 encoding never produces a value > 1.0.
    timestamp_us:
        Monotonic microsecond timestamp of the inference, measured from
        module import time. Used to order detections and to populate
        CommandRequest.timestamp_us.
    bounding_box:
        Optional (x, y, w, h) in pixels, relative to the input frame.
        None for gesture/pose detections that lack a bounding box.
    backend:
        Name of the backend that produced this result (e.g. "yolox", "mediapipe").
        Populated by PerceptionPipeline.run() — not set by the caller.
    """

    detection_type: DetectionType
    label: DetectionLabel
    confidence: float
    timestamp_us: int = field(default_factory=lambda: int(time.monotonic() * 1_000_000))
    bounding_box: tuple[int, int, int, int] | None = None
    backend: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence!r}")
        # Clamp silently — float32 rounding is a transport concern, not a model concern.
        object.__setattr__(self, "confidence", min(self.confidence, _CONFIDENCE_MAX))

    def passes_threshold(self, threshold: float = 0.70) -> bool:
        """True if this detection meets the STM32H5 confidence gate."""
        return self.confidence >= threshold


class PerceptionPipeline(ABC):
    """
    Abstract base for all inference backends.

    Subclasses must implement run(). The governance layer calls run() on
    every captured frame and forwards high-confidence results to the IPC
    command path.
    """

    #: Subclass-level backend name, used to populate DetectionResult.backend.
    backend_name: str = "unknown"

    @abstractmethod
    def run(self, frame: np.ndarray) -> list[DetectionResult]:
        """
        Run inference on a single BGR uint8 frame (H x W x 3 numpy array).

        Returns a list of DetectionResult objects, ordered by descending
        confidence. Returns an empty list if no detections pass the backend's
        internal filtering. Must not raise — return [] on inference error.
        """

    def warm_up(self, frame: np.ndarray) -> None:
        """
        Optional: run one inference to prime JIT/NPU caches.
        Call once before the main capture loop. Default: calls run() once.
        """
        self.run(frame)

    def _stamp(self, result: DetectionResult) -> DetectionResult:
        """Return result with backend populated (used by subclasses)."""
        return DetectionResult(
            detection_type=result.detection_type,
            label=result.label,
            confidence=result.confidence,
            timestamp_us=result.timestamp_us,
            bounding_box=result.bounding_box,
            backend=self.backend_name,
        )
