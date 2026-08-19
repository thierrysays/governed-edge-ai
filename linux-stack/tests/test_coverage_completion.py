"""
Tests for the paths the rest of the suite leaves untouched.

Not filler: everything here is an error branch or a hardware-only branch, and
those are the ones that run at the worst possible moment. Each test states
what real condition it stands in for.
"""

import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from logger import AuditLogger

from governance.filter import GovernanceFilter
from ipc.codec import (
    ActionType,
    Actor,
    CommandRequest,
    FrameError,
    MsgType,
    RejectReason,
    SystemState,
    decode,
    encode,
)
from ipc.mock_peer import MockSTM32H5
from oversight.mock_supervisor import MockR4Supervisor
from perception.base import DetectionResult
from perception.network import DetectionResultClient, DetectionResultServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# IPC codec
# ---------------------------------------------------------------------------

class TestCodecErrorPaths:
    def test_encoding_an_unknown_message_type_is_refused(self):
        """A message class the codec does not know must fail loudly, never
        silently produce a frame the peer will misread."""

        @dataclass(frozen=True)
        class Bogus:
            msg_type: MsgType = MsgType.COMMAND_REQUEST

        with pytest.raises(TypeError, match="Cannot encode Bogus"):
            encode(Bogus())

    def test_frame_shorter_than_a_header_is_refused(self):
        with pytest.raises(FrameError, match="Frame too short"):
            decode(b"\xa5\x01")

    def test_bad_magic_is_refused(self):
        good = bytearray(encode(CommandRequest(
            audit_ref=1, timestamp_us=0, actor=Actor.AI, confidence=0.9,
            action_type=ActionType.HALT, action_param=0,
        )))
        good[0] = 0x5A
        with pytest.raises(FrameError, match="Bad magic"):
            decode(bytes(good))

    def test_length_mismatch_is_refused(self):
        frame = encode(CommandRequest(
            audit_ref=1, timestamp_us=0, actor=Actor.AI, confidence=0.9,
            action_type=ActionType.HALT, action_param=0,
        ))
        with pytest.raises(FrameError, match="length mismatch"):
            decode(frame + b"\x00")

    def test_unknown_message_type_byte_is_refused(self):
        from ipc.codec import crc16_ccitt

        body = bytes([0xA5, 0x77, 0x00, 0x00])
        frame = body + crc16_ccitt(body).to_bytes(2, "little")
        with pytest.raises(FrameError, match="Unknown message type"):
            decode(frame)


# ---------------------------------------------------------------------------
# MockSTM32H5
# ---------------------------------------------------------------------------

class TestMockPeerFaultPaths:
    def test_halted_peer_rejects_with_watchdog_timeout(self):
        """The watchdog has already fired; a later command must be refused
        with the reason that describes why, not a generic failure."""
        with MockSTM32H5(watchdog_ms=50.0) as peer:
            channel = open(peer.device, "rb+", buffering=0)
            try:
                time.sleep(0.2)
                assert peer.state is SystemState.HALTED
                channel.write(encode(CommandRequest(
                    audit_ref=1, timestamp_us=0, actor=Actor.AI, confidence=0.95,
                    action_type=ActionType.HALT, action_param=0,
                )))
                import select as _select

                from ipc.codec import CommandReject, FrameParser
                parser = FrameParser()
                deadline = time.monotonic() + 1.0
                rejects: list = []
                while time.monotonic() < deadline and not rejects:
                    ready, _, _ = _select.select([channel.fileno()], [], [], 0.05)
                    if ready:
                        parser.feed(os.read(channel.fileno(), 512))
                        rejects = [
                            m for m in parser.pop_messages()
                            if isinstance(m, CommandReject)
                        ]
                assert rejects
                assert rejects[0].reason is RejectReason.WATCHDOG_TIMEOUT
            finally:
                channel.close()

    def test_reader_thread_exits_when_select_fails(self):
        """A descriptor that select() itself rejects must end the loop rather
        than spin on the error."""
        import ipc.mock_peer as module

        peer = MockSTM32H5(watchdog_ms=10_000.0)
        with patch.object(module.select, "select", side_effect=OSError("EBADF")):
            peer.start()
            thread = peer._thread   # noqa: SLF001
            deadline = time.monotonic() + 2.0
            while thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not thread.is_alive()
        peer.stop()

    def test_reader_thread_exits_when_the_device_dies(self):
        peer = MockSTM32H5(watchdog_ms=10_000.0).start()
        try:
            os.close(peer._master_fd)  # noqa: SLF001 - the cable is pulled
            thread = peer._thread      # noqa: SLF001
            deadline = time.monotonic() + 2.0
            while thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not thread.is_alive()
        finally:
            peer._master_fd = os.open(os.devnull, os.O_RDWR)  # noqa: SLF001
            peer.stop()


class TestMockSupervisorReaderLoop:
    def test_reader_thread_exits_when_select_fails(self):
        import oversight.mock_supervisor as module

        node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0)
        with patch.object(module.select, "select", side_effect=OSError("EBADF")):
            node.start()
            thread = node._thread   # noqa: SLF001
            deadline = time.monotonic() + 2.0
            while thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not thread.is_alive()
        node.stop()


# ---------------------------------------------------------------------------
# GovernanceFilter
# ---------------------------------------------------------------------------

class TestFilterReadFailure:
    def test_select_failure_while_awaiting_a_response(self, tmp_path):
        """The write lands, then the descriptor goes away underneath: the
        result must be a NULL ack, not an exception out of the safety loop."""
        db = tmp_path / "audit.db"
        audit_logger = AuditLogger(db)
        session_id = audit_logger.open_session()
        sink_r, sink_w = os.pipe()
        dead_r, dead_w = os.pipe()
        os.close(dead_r)
        os.close(dead_w)

        class _WriteOkReadDead:
            def write(self, data: bytes) -> int:
                return os.write(sink_w, data)

            def fileno(self) -> int:
                return dead_r   # closed: select() raises EBADF

        gf = GovernanceFilter(
            logger=audit_logger, session_id=session_id,
            channel=_WriteOkReadDead(), response_timeout_s=0.2,
        )
        try:
            gf.process_frame([DetectionResult("object", "person", 0.91)])
            row = audit_logger.fetch_event(1)
            assert row["command_sent"] == 1
            import sqlite3
            conn = sqlite3.connect(db)
            assert conn.execute("SELECT stm32_ack FROM audit_log").fetchone()[0] is None
            conn.close()
        finally:
            os.close(sink_r)
            os.close(sink_w)
            audit_logger.close()


# ---------------------------------------------------------------------------
# DetectionResult transport
# ---------------------------------------------------------------------------

class TestNetworkFaultPaths:
    def test_send_to_a_dead_listener_raises_after_retrying(self):
        """The UNO Q must not silently drop a frame the VENTUNO Q never got."""
        client = DetectionResultClient(host="127.0.0.1", port=_free_port())
        with pytest.raises(OSError, match="Could not deliver frame"):
            client.send([DetectionResult("object", "person", 0.9)])

    def test_send_of_an_empty_frame_is_a_no_op(self):
        client = DetectionResultClient(host="127.0.0.1", port=_free_port())
        client.send([])   # no connection attempted
        client.close()

    def test_server_survives_a_client_that_disappears(self):
        """A half-written frame must drop the connection, not the server."""
        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()
        received: list = []

        def _serve() -> None:
            for batch in server.frames():
                received.append(batch)
                break

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        time.sleep(0.05)

        rude = socket.create_connection(("127.0.0.1", port))
        rude.sendall(b"\x00\x00\x00\x40")   # promises 64 bytes
        rude.close()                        # delivers none
        time.sleep(0.1)

        good = DetectionResultClient(host="127.0.0.1", port=port)
        good.send([DetectionResult("object", "person", 0.9)])
        t.join(timeout=3.0)
        good.close()
        server.close()
        assert received and received[0][0].label == "person"

    def test_client_without_a_socket_after_connect_is_refused(self):
        """Defensive branch: _connect() returning without a socket must not
        become a silent no-op send."""
        client = DetectionResultClient(host="127.0.0.1", port=_free_port())
        with patch.object(DetectionResultClient, "_connect", lambda self: None), \
             pytest.raises(OSError, match="Could not deliver frame"):
            client.send([DetectionResult("object", "person", 0.9)])

    def test_frames_before_start_is_refused(self):
        server = DetectionResultServer(host="127.0.0.1", port=_free_port())
        with pytest.raises(RuntimeError, match="Call start"):
            next(server.frames())

    def test_close_before_start_is_a_no_op(self):
        DetectionResultServer(host="127.0.0.1", port=_free_port()).close()


# ---------------------------------------------------------------------------
# UNO Q perception service
# ---------------------------------------------------------------------------

class TestUnoQService:
    def test_production_backends_are_used_when_importable(self):
        """The stub fallback is the CI path. This exercises the other one."""
        import types

        from perception.base import PerceptionPipeline

        class _Fake(PerceptionPipeline):
            backend_name = "fake"

            def run(self, frame):
                return []

        module = types.ModuleType("perception.backends_impl")
        module.YOLOXBackend = _Fake
        module.MediaPipeBackend = _Fake
        module.PoseNetBackend = _Fake

        from perception import uno_q_service

        with patch.dict(sys.modules, {"perception.backends_impl": module}):
            backends = uno_q_service._build_backends()  # noqa: SLF001
        assert [type(b).__name__ for b in backends] == ["_Fake"] * 3

    def test_stop_breaks_the_capture_loop(self):
        from perception.capture import SyntheticFrameSource
        from perception.uno_q_service import PerceptionService

        client = MagicMock()
        pipeline = MagicMock()
        pipeline.run.return_value = []
        service = PerceptionService(
            source=SyntheticFrameSource(fps=200), pipeline=pipeline, client=client
        )

        def _stop_soon() -> None:
            time.sleep(0.05)
            service.stop()

        threading.Thread(target=_stop_soon, daemon=True).start()
        service.run()   # must return once stop() lands

    def test_max_frames_stops_the_capture_loop(self):
        from perception.capture import SyntheticFrameSource
        from perception.uno_q_service import PerceptionService

        pipeline = MagicMock()
        pipeline.run.return_value = []
        service = PerceptionService(
            source=SyntheticFrameSource(fps=500), pipeline=pipeline,
            client=MagicMock(), max_frames=3,
        )
        service.run()
        assert pipeline.run.call_count == 3

    def test_v4l2_source_is_selected_by_flag(self):
        from perception import uno_q_service

        with patch.object(uno_q_service, "V4L2FrameSource") as source, \
             patch.object(uno_q_service, "PerceptionService") as service, \
             patch.object(uno_q_service, "DetectionResultClient"):
            service.return_value.run = MagicMock()
            assert uno_q_service.main(["--source", "v4l2", "--device", "2"]) == 0
        source.assert_called_once_with(device_index=2)

    def test_signal_handler_stops_the_service(self):
        import signal as signal_module

        from perception import uno_q_service

        captured = {}

        def _capture(sig, handler):
            captured[sig] = handler

        with patch.object(uno_q_service, "PerceptionService") as service, \
             patch.object(uno_q_service, "DetectionResultClient"), \
             patch.object(signal_module, "signal", _capture):
            instance = service.return_value
            instance.run = MagicMock()
            uno_q_service.main([])
            captured[signal_module.SIGINT](signal_module.SIGINT, None)
        instance.stop.assert_called_once()


# ---------------------------------------------------------------------------
# VENTUNO Q governance service
# ---------------------------------------------------------------------------

class TestVentunoQService:
    def test_oversight_node_on_a_real_device_path(self):
        """--supervisor /dev/tty... is the deployment path; 'mock' is not."""
        from governance.ventuno_q_service import _open_supervisor

        node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
        try:
            link, spawned = _open_supervisor(node.device)
            assert spawned is None          # nothing was spawned: a real device
            assert link is not None
            time.sleep(0.05)
            assert node.stats.heartbeats_received >= 1
            link.close()
        finally:
            node.stop()

    def test_alvik_on_a_real_device_path(self):
        """--alvik /dev/tty... likewise."""
        from governance.ventuno_q_service import main

        peer = MockSTM32H5(watchdog_ms=10_000.0).start()
        try:
            with patch("governance.ventuno_q_service.GovernanceService") as service, \
                 patch("governance.ventuno_q_service.DetectionResultServer"):
                service.return_value.run = MagicMock()
                result = main([
                    "--alvik", peer.device, "--supervisor", "none", "--db", ":memory:",
                ])
            assert result == 0
        finally:
            peer.stop()

    def test_signal_handler_stops_the_service(self):
        import signal as signal_module

        from governance import ventuno_q_service

        captured = {}

        with patch.object(ventuno_q_service, "GovernanceService") as service, \
             patch.object(ventuno_q_service, "DetectionResultServer"), \
             patch.object(signal_module, "signal", lambda s, h: captured.__setitem__(s, h)):
            instance = service.return_value
            instance.run = MagicMock()
            ventuno_q_service.main(["--supervisor", "none", "--db", ":memory:"])
            captured[signal_module.SIGTERM](signal_module.SIGTERM, None)
        instance.stop.assert_called_once()

    def test_stop_ends_the_frame_loop(self):
        from governance.ventuno_q_service import GovernanceService

        server = MagicMock(spec=DetectionResultServer)
        gf = MagicMock()
        service = GovernanceService(server=server, gf=gf, max_frames=None)

        def _frames():
            yield [DetectionResult("object", "person", 0.9)]
            service.stop()
            yield [DetectionResult("object", "person", 0.9)]
            yield [DetectionResult("object", "person", 0.9)]

        server.frames.return_value = _frames()
        service.run()
        assert gf.process_frame.call_count == 1

    def test_max_frames_stops_the_governance_loop(self):
        from governance.ventuno_q_service import GovernanceService

        server = MagicMock(spec=DetectionResultServer)
        gf = MagicMock()
        service = GovernanceService(server=server, gf=gf, max_frames=2)

        def _endless():
            while True:
                yield [DetectionResult("object", "person", 0.9)]

        server.frames.return_value = _endless()
        service.run()
        assert gf.process_frame.call_count == 2

    def test_oversight_optional_disables_fail_closed(self):
        """Bench mode: the operator has said there is no oversight node."""
        from governance.ventuno_q_service import _open_supervisor

        node = MockR4Supervisor(heartbeat_timeout_ms=10_000.0).start()
        try:
            link, _ = _open_supervisor(node.device, fail_closed=False)
            assert link._fail_closed is False  # noqa: SLF001
            link.close()
        finally:
            node.stop()
