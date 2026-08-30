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

from logger import AuditLogger

from governance.filter import GovernanceFilter
from governance.ventuno_q_service import GovernanceService
from ipc.codec import OverrideReason
from ipc.mock_peer import MockSTM32H5
from oversight.mock_supervisor import MockR4Supervisor
from oversight.supervisor_link import SupervisorLink
from perception.base import DetectionResult
from perception.network import DetectionResultClient, DetectionResultServer

SETTLE_S = 0.15
SILENCE_TIMEOUT_MS = 200.0


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

    def test_main_logs_when_sentry_enabled(self):
        from governance.ventuno_q_service import main
        with (
            patch("governance.ventuno_q_service.init_sentry", return_value=True),
            patch("governance.ventuno_q_service.GovernanceService") as MockSvc,
            patch("governance.ventuno_q_service.DetectionResultServer") as MockSrv,
        ):
            inst = MockSvc.return_value
            inst.run = MagicMock()
            srv_inst = MockSrv.return_value
            srv_inst.start = MagicMock()
            result = main(["--alvik", "mock", "--db", ":memory:"])
        assert result == 0


# ---------------------------------------------------------------------------
# Silence at the service level
# ---------------------------------------------------------------------------

class TestBlindServiceFallsSilent:
    """
    A running service with nothing to govern must stop reassuring the arbiter.

    TestSilenceWhenNotGoverning in test_supervisor_link.py proves the link
    emits nothing when record() is not called. This proves the consequence one
    level up, where the failure actually happens: the service still running,
    its socket still open, blocked in frames() with no perception arriving.
    That is the failure a network introduces, and the one Part 10's Test 4 does
    not cover, because Test 4 stops the process instead of blinding it.

    Nothing here is arranged to make the system fall closed. The service is
    left alone and the arbiter reaches the conclusion by itself, from silence.
    """

    def test_an_idle_perception_link_latches_the_arbiter(self, tmp_path):
        audit_logger = AuditLogger(tmp_path / "audit.db")
        session_id = audit_logger.open_session(board_serial="RIG")

        peer = MockSTM32H5(watchdog_ms=10_000.0).start()
        actuation = open(peer.device, "rb+", buffering=0)

        node = MockR4Supervisor(heartbeat_timeout_ms=SILENCE_TIMEOUT_MS).start()
        oversight = open(node.device, "rb+", buffering=0)
        link = SupervisorLink(oversight, heartbeat_interval_s=0.0)

        gf = GovernanceFilter(
            logger=audit_logger,
            session_id=session_id,
            channel=actuation,
            response_timeout_s=0.5,
            supervisor=link,
        )

        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()
        service = GovernanceService(server=server, gf=gf)
        thread = threading.Thread(target=service.run, daemon=True)
        thread.start()

        client = None
        try:
            time.sleep(0.05)
            client = DetectionResultClient(host="127.0.0.1", port=port)
            client.send([_make_detection("person")])
            time.sleep(SETTLE_S)

            # It governed once. The digest was witnessed and the heartbeat
            # that followed it released the contact.
            heard = node.stats.heartbeats_received
            assert heard >= 1
            assert node.stats.digests_received >= 1
            assert node.override_active is False
            assert node.motor_power_cut is False

            # The perception link goes quiet here. Nothing else changes: the
            # service is up, the socket is open, the oversight cable is intact.
            time.sleep(3 * SILENCE_TIMEOUT_MS / 1000.0)

            assert node.stats.heartbeats_received == heard
            assert node.override_active is True
            assert node.override_reason is OverrideReason.GOVERNANCE_HEARTBEAT_LOST
            assert node.motor_power_cut is True
        finally:
            service.stop()
            if client is not None:
                client.close()
            server.close()
            thread.join(timeout=1.0)
            link.close()
            node.stop()
            actuation.close()
            peer.stop()
            audit_logger.close()
