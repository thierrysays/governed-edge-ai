"""
UNO Q 4GB perception service.

Entry point for the perception node. Captures frames from the UNO Q ISP
camera via V4L2, runs the configured perception backends, and forwards
DetectionResult objects to the VENTUNO Q governance service over TCP.

Usage:
  python -m perception.uno_q_service [--device 0] [--host 192.168.x.x] [--port 9100]

The service runs until interrupted (SIGINT / Ctrl-C). Each camera frame is
processed by all registered backends; results are batched per frame and sent
as a single TCP message to the VENTUNO Q.

Hardware-free mode:
  Set --source synthetic (default) to use SyntheticFrameSource. This lets
  the full pipeline run without a camera connected, which is useful for
  software integration testing before the camera module arrives.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Sequence

import numpy as np

from observability import init_sentry
from perception.base import DetectionResult, PerceptionPipeline
from perception.capture import FrameSource, SyntheticFrameSource, V4L2FrameSource
from perception.network import DetectionResultClient

log = logging.getLogger(__name__)


class MultiBackendPipeline:
    """
    Runs multiple PerceptionPipeline backends on each frame.

    Results from all backends are merged into a single list per frame,
    sorted by descending confidence. The GovernanceFilter on VENTUNO Q
    will select the highest-confidence detection for command dispatch.
    """

    def __init__(self, backends: list[PerceptionPipeline]) -> None:
        self._backends = backends

    def run(self, frame: np.ndarray) -> list[DetectionResult]:
        results: list[DetectionResult] = []
        for backend in self._backends:
            try:
                results.extend(backend.run(frame))
            except Exception:
                log.exception("Backend %s raised on run()", backend.backend_name)
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results


class PerceptionService:
    """
    Main UNO Q perception loop.

    Parameters
    ----------
    source:
        Frame source (V4L2 camera or synthetic).
    pipeline:
        Multi-backend pipeline instance.
    client:
        DetectionResultClient pointing at the VENTUNO Q.
    max_frames:
        Stop after this many frames (None = run forever). Used in tests.
    """

    def __init__(
        self,
        source: FrameSource,
        pipeline: MultiBackendPipeline,
        client: DetectionResultClient,
        max_frames: int | None = None,
    ) -> None:
        self._source = source
        self._pipeline = pipeline
        self._client = client
        self._max_frames = max_frames
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        count = 0
        for frame in self._source.frames():
            if not self._running:
                break
            if self._max_frames is not None and count >= self._max_frames:
                break
            results = self._pipeline.run(frame)
            if results:
                try:
                    self._client.send(results)
                except OSError as exc:
                    log.warning("Failed to send frame %d to VENTUNO Q: %s", count, exc)
            count += 1
        self._source.close()
        self._client.close()
        log.info("Perception service stopped after %d frames.", count)


def _build_backends() -> list[PerceptionPipeline]:
    """
    Load production backends when available; fall back to stubs.

    Import order: try the real backend first; catch ImportError and fall
    back to the stub. This pattern lets the same service binary run on
    the UNO Q with NPU weights and on CI without them.
    """
    backends: list[PerceptionPipeline] = []

    try:
        from perception.backends_impl import YOLOXBackend
        backends.append(YOLOXBackend())
        log.info("Loaded YOLO-X backend")
    except (ImportError, RuntimeError, OSError):
        from perception.backends import StubObjectDetector
        backends.append(StubObjectDetector())
        log.info("YOLO-X unavailable: using StubObjectDetector")

    try:
        from perception.backends_impl import MediaPipeBackend
        backends.append(MediaPipeBackend())
        log.info("Loaded MediaPipe backend")
    except (ImportError, RuntimeError, OSError):
        from perception.backends import StubGestureRecognizer
        backends.append(StubGestureRecognizer())
        log.info("MediaPipe unavailable: using StubGestureRecognizer")

    try:
        from perception.backends_impl import PoseNetBackend
        backends.append(PoseNetBackend())
        log.info("Loaded PoseNet backend")
    except (ImportError, RuntimeError, OSError):
        from perception.backends import StubPoseEstimator
        backends.append(StubPoseEstimator())
        log.info("PoseNet unavailable: using StubPoseEstimator")

    return backends


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if init_sentry("uno-q-perception"):
        log.info("Sentry error reporting enabled (SENTRY_DSN set).")

    parser = argparse.ArgumentParser(description="UNO Q 4GB perception service")
    parser.add_argument("--device", type=int, default=0,
                        help="V4L2 device index (default 0)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="VENTUNO Q hostname/IP (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9100,
                        help="VENTUNO Q TCP port (default 9100)")
    parser.add_argument("--source", choices=["v4l2", "synthetic"], default="synthetic",
                        help="Frame source (default: synthetic)")
    parser.add_argument("--fps", type=float, default=10.0,
                        help="Synthetic source FPS (default 10)")
    args = parser.parse_args(argv)

    if args.source == "v4l2":
        source: FrameSource = V4L2FrameSource(device_index=args.device)
    else:
        source = SyntheticFrameSource(fps=args.fps)

    backends = _build_backends()
    pipeline = MultiBackendPipeline(backends)
    client = DetectionResultClient(host=args.host, port=args.port)
    service = PerceptionService(source=source, pipeline=pipeline, client=client)

    def _shutdown(sig: int, frame: object) -> None:
        log.info("Shutting down (signal %d).", sig)
        service.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    service.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
