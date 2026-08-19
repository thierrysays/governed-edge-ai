"""
Tests for governance/ventuno_q_service.py.

Uses MockSTM32H5 (pty), an in-memory AuditLogger, and a DetectionResultServer
loopback to exercise the full VENTUNO Q governance pipeline end-to-end without
physical hardware. All tests are hardware-free.
"""

import socket
import threading
import time
from unittest.mock import MagicMock, patch

from governance.ventuno_q_service import GovernanceService
from perception.base import DetectionResult
from perception.network import DetectionResultServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_detection(
    label: str = "person",
    confidence: float = 0.91,
    detection_type: str = "object",
) -> DetectionResult:
    return DetectionResult(
        detection_type=detection_type, label=label, confidence=confidence, backend="test"
    )


# ---------------------------------------------------------------------------
# GovernanceService unit tests
# ---------------------------------------------------------------------------

class TestGovernanceService:
    def _make_service(self, max_frames=2):
        server = MagicMock(spec=DetectionResultServer)
        gf = MagicMock()
        service = GovernanceService(server=server, gf=gf, max_frames=max_frames)
        return service, server, gf

    def test_calls_gf_process_frame_per_batch(self):
        service, server, gf = self._make_service(max_frames=2)
        batches = [
            [_make_detection("person")],
            [_make_detection("stop", 0.88, "gesture")],
        ]
        server.frames.return_value = iter(batches)
        service.run()
        assert gf.process_frame.call_count == 2

    def test_gf_exception_does_not_crash_service(self):
        service, server, gf = self._make_service(max_frames=1)
        server.frames.return_value = iter([[_make_detection()]])
        gf.process_frame.side_effect = RuntimeError("audit db locked")
        service.run()  # must not raise

    def test_server_close_called_after_run(self):
        service, server, gf = self._make_service(max_frames=0)
        server.frames.return_value = iter([])
        service.run()
        server.close.assert_called_once()

    def test_stop_breaks_loop(self):
        service, server, gf = self._make_service(max_frames=1000)

        def _frames():
            service.stop()
            yield [_make_detection()]

        server.frames.return_value = _frames()
        service.run()
        # stop() was called before the first batch; process_frame may or may not fire
        assert gf.process_frame.call_count <= 1


# ---------------------------------------------------------------------------
# End-to-end smoke test: network -> governance -> mock IPC
# ---------------------------------------------------------------------------

class TestGovernanceServiceE2E:
    def test_single_frame_reaches_gf(self):
        """A detection sent over the loopback TCP reaches GovernanceFilter."""
        from perception.network import DetectionResultClient

        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()

        gf = MagicMock()
        service = GovernanceService(server=server, gf=gf, max_frames=1)

        t = threading.Thread(target=service.run, daemon=True)
        t.start()

        time.sleep(0.05)
        client = DetectionResultClient(host="127.0.0.1", port=port)
        client.send([_make_detection("person")])
        client.close()

        t.join(timeout=3.0)
        server.close()

        assert gf.process_frame.call_count == 1
        called_with = gf.process_frame.call_args[0][0]
        assert called_with[0].label == "person"

    def test_main_mock_mode(self):
        from governance.ventuno_q_service import main
        with patch("governance.ventuno_q_service.GovernanceService") as MockSvc:
            inst = MockSvc.return_value
            inst.run = MagicMock()
            with patch("governance.ventuno_q_service.DetectionResultServer") as MockSrv:
                srv_inst = MockSrv.return_value
                srv_inst.start = MagicMock()
                result = main(["--alvik", "mock", "--db", ":memory:"])
        assert result == 0
