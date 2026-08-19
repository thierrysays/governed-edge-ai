"""
Production perception backends for the UNO Q 4GB.

Each backend attempts to import its required library. If the library is absent
(e.g. on CI or a development workstation) the backend raises ImportError, and
uno_q_service._build_backends() falls back to the corresponding stub.

YOLO-X backend:    requires ultralytics (pip install ultralytics)
MediaPipe backend: requires mediapipe (pip install mediapipe)
PoseNet backend:   requires mediapipe pose module

All three backends are designed to run on the Qualcomm QRB2210 CPU of the
UNO Q 4GB. NPU-accelerated inference via the Qualcomm AI Hub SDK can replace
the CPU path in a future iteration once the SDK supports the QRB2210 target.
"""

from __future__ import annotations

import numpy as np

from perception.base import DetectionResult, PerceptionPipeline

# Detection labels used by the GovernanceFilter command map
_YOLOX_PERSON_CLASS_ID = 0   # COCO class 0 = person

_GESTURE_LABELS = {
    "Closed_Fist":     "stop",
    "Thumb_Up":        "thumbs_up",
    "Thumb_Down":      "thumbs_down",
    "Open_Palm":       "stop",
    "Pointing_Up":     "thumbs_up",
    "Victory":         None,       # no mapped command; logged as suppressed
    "ILoveYou":        None,
}

_PROXIMITY_THRESHOLD_PX = 200  # skeleton keypoint distance below which breach fires


class YOLOXBackend(PerceptionPipeline):
    """
    Object detection using YOLO-X (via ultralytics YOLO interface).

    Detects persons and equipment in the Alvik's operational workspace.
    The model runs on CPU; a QRB2210-optimised ONNX runtime can be
    substituted by overriding model_path.
    """

    backend_name = "yolox"

    def __init__(self, model_path: str = "yolov8n.pt", conf: float = 0.50) -> None:
        from ultralytics import YOLO
        self._model = YOLO(model_path)
        self._conf = conf

    def run(self, frame: np.ndarray) -> list[DetectionResult]:
        try:
            results = self._model.predict(frame, conf=self._conf, verbose=False)
        except Exception:
            return []

        detections: list[DetectionResult] = []
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                label = "person" if cls_id == _YOLOX_PERSON_CLASS_ID else "robot_part"
                detections.append(self._stamp(DetectionResult(
                    detection_type="object",
                    label=label,
                    confidence=conf,
                    bounding_box=(int(x1), int(y1), int(x2 - x1), int(y2 - y1)),
                )))
        return detections


class MediaPipeBackend(PerceptionPipeline):
    """
    Gesture recognition using MediaPipe Gesture Recognizer.

    Recognises hand gestures for operator control of the Alvik:
    thumbs-up (move forward), thumbs-down (move backward), fist (stop).
    """

    backend_name = "mediapipe"

    def __init__(self, model_path: str = "gesture_recognizer.task") -> None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision as mp_vision

        base_opts = mp_tasks.BaseOptions(model_asset_path=model_path)
        opts = mp_vision.GestureRecognizerOptions(base_options=base_opts)
        self._recognizer = mp_vision.GestureRecognizer.create_from_options(opts)
        self._mp = mp

    def run(self, frame: np.ndarray) -> list[DetectionResult]:
        try:
            import mediapipe as mp
            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB,
                data=frame[:, :, ::-1],  # BGR to RGB
            )
            result = self._recognizer.recognize(mp_image)
        except Exception:
            return []

        detections: list[DetectionResult] = []
        if result.gestures:
            for gesture_group in result.gestures:
                if not gesture_group:
                    continue
                top = gesture_group[0]
                label = _GESTURE_LABELS.get(top.category_name)
                if label is not None:
                    detections.append(self._stamp(DetectionResult(
                        detection_type="gesture",
                        label=label,
                        confidence=float(top.score),
                    )))
        return detections


class PoseNetBackend(PerceptionPipeline):
    """
    Proximity safety boundary detection via MediaPipe Pose (MoveNet Lightning).

    Fires a "proximity_breach" detection when skeleton keypoints indicate
    the operator has entered the Alvik's operational boundary.
    """

    backend_name = "posenet"

    def __init__(self) -> None:
        import mediapipe as mp
        self._pose = mp.solutions.pose.Pose(
            model_complexity=0,          # MoveNet Lightning (fastest)
            enable_segmentation=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def run(self, frame: np.ndarray) -> list[DetectionResult]:
        try:
            rgb = frame[:, :, ::-1]
            result = self._pose.process(rgb)
        except Exception:
            return []

        if not result.pose_landmarks:
            return []

        h, w = frame.shape[:2]
        lm = result.pose_landmarks.landmark

        # Use nose (0) and wrist landmarks to estimate proximity
        nose = lm[0]
        left_wrist = lm[15]
        right_wrist = lm[16]

        # Pixel distance between nose and nearest wrist
        nose_x, nose_y = nose.x * w, nose.y * h
        for wrist in (left_wrist, right_wrist):
            dx = (wrist.x * w) - nose_x
            dy = (wrist.y * h) - nose_y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < _PROXIMITY_THRESHOLD_PX:
                confidence = float(min(1.0 - dist / _PROXIMITY_THRESHOLD_PX, 0.9999))
                return [self._stamp(DetectionResult(
                    detection_type="pose",
                    label="proximity_breach",
                    confidence=confidence,
                ))]

        return []
