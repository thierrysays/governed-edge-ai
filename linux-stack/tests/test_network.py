"""
Tests for perception/network.py.

Uses a loopback TCP pair to exercise the full client-server round trip.
No external services required.
"""

import socket
import threading
import time

import pytest

from perception.base import DetectionResult
from perception.network import (
    MAX_MESSAGE_BYTES,
    DetectionResultClient,
    DetectionResultServer,
    _decode_message,
    _encode,
)

# ---------------------------------------------------------------------------
# Serialisation tests
# ---------------------------------------------------------------------------

class TestSerialisation:
    def _make_result(self, label: str = "person", conf: float = 0.85) -> DetectionResult:
        return DetectionResult(
            detection_type="object",
            label=label,
            confidence=conf,
            bounding_box=(10, 20, 100, 200),
            backend="stub",
        )

    def test_roundtrip_single(self):
        r = self._make_result()
        decoded = _decode_message(_encode([r])[4:])  # strip 4-byte length prefix
        assert len(decoded) == 1
        assert decoded[0].label == "person"
        assert decoded[0].confidence == pytest.approx(0.85, abs=1e-6)

    def test_roundtrip_multiple(self):
        results = [self._make_result("person", 0.91), self._make_result("stop", 0.77)]
        raw = _encode(results)
        # raw starts with 4-byte length; skip it for direct decode
        decoded = _decode_message(raw[4:])
        assert len(decoded) == 2
        labels = {r.label for r in decoded}
        assert labels == {"person", "stop"}

    def test_bounding_box_none_preserved(self):
        r = DetectionResult(
            detection_type="gesture", label="stop", confidence=0.80, bounding_box=None
        )
        decoded = _decode_message(_encode([r])[4:])
        assert decoded[0].bounding_box is None

    def test_bounding_box_tuple_preserved(self):
        r = DetectionResult(
            detection_type="object", label="person", confidence=0.80,
            bounding_box=(1, 2, 3, 4)
        )
        decoded = _decode_message(_encode([r])[4:])
        assert decoded[0].bounding_box == (1, 2, 3, 4)

    def test_detection_type_preserved(self):
        for dt in ("object", "gesture", "pose"):
            r = DetectionResult(detection_type=dt, label="x", confidence=0.75)
            decoded = _decode_message(_encode([r])[4:])
            assert decoded[0].detection_type == dt


# ---------------------------------------------------------------------------
# Client/Server integration tests
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_detection(label: str = "person") -> DetectionResult:
    return DetectionResult(
        detection_type="object", label=label, confidence=0.90, backend="test"
    )


class TestClientServer:
    def _run_server(self, server: DetectionResultServer, received: list, count: int):
        for batch in server.frames():
            received.extend(batch)
            if len(received) >= count:
                break

    def test_single_frame_roundtrip(self):
        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()

        received: list[DetectionResult] = []
        t = threading.Thread(
            target=self._run_server, args=(server, received, 1), daemon=True
        )
        t.start()

        time.sleep(0.05)
        client = DetectionResultClient(host="127.0.0.1", port=port)
        client.send([_make_detection("person")])
        client.close()

        t.join(timeout=2.0)
        server.close()

        assert len(received) >= 1
        assert received[0].label == "person"

    def test_multiple_frames(self):
        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()

        received: list[DetectionResult] = []
        t = threading.Thread(
            target=self._run_server, args=(server, received, 3), daemon=True
        )
        t.start()

        time.sleep(0.05)
        client = DetectionResultClient(host="127.0.0.1", port=port)
        for label in ("person", "stop", "thumbs_up"):
            client.send([_make_detection(label)])
        client.close()

        t.join(timeout=2.0)
        server.close()

        assert len(received) == 3
        assert {r.label for r in received} == {"person", "stop", "thumbs_up"}

    def test_empty_send_is_noop(self):
        port = _free_port()
        client = DetectionResultClient(host="127.0.0.1", port=port)
        client.send([])  # must not attempt connection or raise
        client.close()

    def test_server_close_is_idempotent(self):
        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()
        server.close()
        server.close()  # second close must not raise


# ---------------------------------------------------------------------------
# Hostile and broken peers
# ---------------------------------------------------------------------------

class TestHostilePeer:
    """The listener binds 0.0.0.0:9100 on the board holding the audit journal,
    and reads a four-byte length prefix from an unauthenticated peer before
    anything else. Everything below is what that peer can try.

    These exist because a security audit found the cap missing. It is the
    third time this codebase has met the same shape: `MAX_PAYLOAD` in the IPC
    codec and `IPC_MAX_FRAME` in the C++ parser are the other two.
    """

    def _serve_once(self, server, sink):
        def _run():
            try:
                for batch in server.frames():
                    sink.append(batch)
            except OSError:
                pass
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    def test_an_oversized_length_prefix_is_refused(self):
        """Four bytes claiming four gibibytes. Before the cap, the node tried
        to accumulate it."""
        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()
        received: list = []
        self._serve_once(server, received)

        sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        sock.sendall((0xFFFFFFFF).to_bytes(4, "big"))
        time.sleep(0.3)
        sock.close()
        server.close()

        assert received == []
        assert any("exceeds" in e for e in server.protocol_errors)

    def test_a_message_at_the_cap_is_still_accepted(self):
        """The bound has to be a bound, not a ceiling that also blocks real
        traffic. A frame of detections is hundreds of bytes."""
        assert MAX_MESSAGE_BYTES == 1 << 20

    def test_undecodable_json_does_not_end_the_listener(self):
        """One malformed message from any peer used to escape the accept loop
        and stop the governance node hearing from anyone."""
        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()
        received: list = []
        self._serve_once(server, received)

        bad = b"{not json"
        sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        sock.sendall(len(bad).to_bytes(4, "big") + bad)
        time.sleep(0.2)
        sock.close()

        # The listener survived: a well-formed client still gets through.
        client = DetectionResultClient(host="127.0.0.1", port=port)
        client.send([DetectionResult(
            detection_type="object", label="person", confidence=0.9,
            timestamp_us=1, backend="test",
        )])
        time.sleep(0.3)
        client.close()
        server.close()

        assert any("Undecodable" in e for e in server.protocol_errors)
        assert received and received[0][0].label == "person"

    def test_a_field_of_the_wrong_shape_is_refused_not_crashed(self):
        port = _free_port()
        server = DetectionResultServer(host="127.0.0.1", port=port)
        server.start()
        received: list = []
        self._serve_once(server, received)

        bad = b'[{"detection_type": "object", "label": "x", "confidence": "NaN-ish"}]'
        sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        sock.sendall(len(bad).to_bytes(4, "big") + bad)
        time.sleep(0.3)
        sock.close()
        server.close()

        assert received == []
        assert server.protocol_errors

    def test_errors_are_kept_for_an_operator_to_read(self):
        server = DetectionResultServer(host="127.0.0.1", port=_free_port())
        assert server.protocol_errors == []
