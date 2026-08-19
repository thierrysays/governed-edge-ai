"""
Network transport for DetectionResult objects between UNO Q and VENTUNO Q.

Protocol: length-prefixed JSON over TCP.
  - 4-byte big-endian uint32: byte length of the UTF-8 JSON payload
  - JSON payload: list of serialised DetectionResult dicts

The UNO Q side runs DetectionResultClient (connect, send one frame's results).
The VENTUNO Q side runs DetectionResultServer (listen, yield frame results).

Design notes:
  - TCP provides ordering and reliability; no application-level retry needed.
  - JSON is chosen over the binary IPC codec because DetectionResult is
    Python-to-Python; the binary codec is reserved for the VENTUNO Q to
    Alvik STM32 link where MicroPython parses it.
  - Each send() call transmits all detections from one camera frame as a
    single message, preserving frame identity for the GovernanceFilter.
"""

from __future__ import annotations

import contextlib
import json
import socket
import struct
from collections.abc import Generator
from typing import Any

from perception.base import DetectionResult

_LEN_FMT = struct.Struct(">I")  # 4-byte big-endian uint32


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _result_to_dict(r: DetectionResult) -> dict[str, Any]:
    return {
        "detection_type": r.detection_type,
        "label": r.label,
        "confidence": r.confidence,
        "timestamp_us": r.timestamp_us,
        "bounding_box": list(r.bounding_box) if r.bounding_box else None,
        "backend": r.backend,
    }


def _dict_to_result(d: dict[str, Any]) -> DetectionResult:
    bb = d.get("bounding_box")
    return DetectionResult(
        detection_type=d["detection_type"],
        label=str(d["label"]),
        confidence=float(d["confidence"]),
        timestamp_us=int(d["timestamp_us"]),
        bounding_box=tuple(int(x) for x in bb) if bb else None,  # type: ignore[arg-type]
        backend=str(d.get("backend", "")),
    )


def _encode(results: list[DetectionResult]) -> bytes:
    payload = json.dumps([_result_to_dict(r) for r in results]).encode()
    return _LEN_FMT.pack(len(payload)) + payload


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("Connection closed by peer")
        buf.extend(chunk)
    return bytes(buf)


def _decode_message(raw: bytes) -> list[DetectionResult]:
    return [_dict_to_result(d) for d in json.loads(raw.decode())]


# ---------------------------------------------------------------------------
# Client (UNO Q side)
# ---------------------------------------------------------------------------

class DetectionResultClient:
    """
    TCP client that runs on the UNO Q.

    Connects to the VENTUNO Q listener and sends one frame's detections
    per send() call. Reconnects automatically on connection loss.
    """

    def __init__(self, host: str, port: int, timeout_s: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout_s
        self._sock: socket.socket | None = None

    def send(self, results: list[DetectionResult]) -> None:
        """Send all detections from one frame. Reconnects if needed."""
        if not results:
            return
        msg = _encode(results)
        for _ in range(2):
            try:
                if self._sock is None:
                    self._connect()
                if self._sock is None:
                    raise OSError("connect() succeeded but socket is still None")
                self._sock.sendall(msg)
                return
            except OSError:
                self._close()
        raise OSError(
            f"Could not deliver frame to VENTUNO Q at {self._host}:{self._port}"
        )

    def close(self) -> None:
        self._close()

    def _connect(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self._timeout)
        s.connect((self._host, self._port))
        self._sock = s

    def _close(self) -> None:
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None


# ---------------------------------------------------------------------------
# Server (VENTUNO Q side)
# ---------------------------------------------------------------------------

class DetectionResultServer:
    """
    TCP server that runs on the VENTUNO Q.

    Accepts connections from the UNO Q and yields frame batches
    (list[DetectionResult]) as they arrive.
    """

    # Binds every interface by design: the UNO Q reaches the VENTUNO Q over
    # whichever link is up (Wi-Fi or USB-C ethernet gadget), and which one is
    # not known at start-up. The boundary is the operator's own LAN, as the
    # deployment section of docs/architecture.md states. Pass an explicit host
    # to narrow it.
    def __init__(self, host: str = "0.0.0.0", port: int = 9100) -> None:  # noqa: S104  # nosec B104
        self._host = host
        self._port = port
        self._server: socket.socket | None = None

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self._host, self._port))
        s.listen(1)
        self._server = s

    def frames(self) -> Generator[list[DetectionResult], None, None]:
        """
        Yield frame batches from the UNO Q connection indefinitely.

        Accepts one connection at a time; reconnects on drop.
        """
        if self._server is None:
            raise RuntimeError("Call start() before frames()")
        while True:
            conn, _ = self._server.accept()
            try:
                yield from self._read_connection(conn)
            except (EOFError, OSError):
                conn.close()

    def _read_connection(
        self, conn: socket.socket
    ) -> Generator[list[DetectionResult], None, None]:
        while True:
            header = _recv_exactly(conn, 4)
            (length,) = _LEN_FMT.unpack(header)
            raw = _recv_exactly(conn, length)
            results = _decode_message(raw)
            if results:
                yield results

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
