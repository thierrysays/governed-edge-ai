"""
VENTUNO Q governance service.

Entry point for the governance node. Listens for DetectionResult frames
from the UNO Q over TCP, runs GovernanceFilter for each frame (log-before-act,
confidence gate, one command per frame), publishes each audit row to the UNO
R4 WiFi oversight node, and forwards audited CommandRequest frames to the
Alvik via the USB-C UART IPC channel.

Usage:
  python -m governance.ventuno_q_service \
      [--listen 0.0.0.0] [--port 9100] \
      [--alvik /dev/ttyACM0] [--supervisor /dev/ttyACM1] \
      [--db /data/audit.db]

The service runs until interrupted (SIGINT / Ctrl-C). The audit SQLite
database is kept open across the session; stm32_ack is updated after each
CommandAck / CommandReject from the Alvik.

Hardware-free mode:
  Set --alvik mock and --supervisor mock to spawn a MockSTM32H5 pty and a
  MockR4Supervisor pty; the governance, oversight and IPC layers run
  identically. This is the mode used by make smoke and make test.

Oversight:
  --supervisor none runs the three-board configuration with no independent
  oversight tier. The service warns on that path: it is the arrangement the
  R4 was added to correct, not a supported deployment.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from collections.abc import Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "audit-service"))

from logger import AuditLogger  # noqa: E402

from governance.filter import DEFAULT_COMMAND_MAP, GovernanceFilter  # noqa: E402
from ipc.mock_peer import MockSTM32H5  # noqa: E402
from oversight.mock_supervisor import MockR4Supervisor  # noqa: E402
from oversight.supervisor_link import SupervisorLink  # noqa: E402
from perception.network import DetectionResultServer  # noqa: E402

log = logging.getLogger(__name__)

# Binds every interface by design: see the note on DetectionResultServer in
# perception/network.py. Pass --listen to narrow it to one address.
DEFAULT_LISTEN_ADDRESS = "0.0.0.0"  # noqa: S104  # nosec B104


class GovernanceService:
    """
    Receives DetectionResult batches from UNO Q and drives GovernanceFilter.

    Parameters
    ----------
    server:
        DetectionResultServer already bound and started.
    gf:
        GovernanceFilter wired to the audit logger and the Alvik IPC channel.
    max_frames:
        Stop after this many frames (None = run forever). Used in tests.
    """

    def __init__(
        self,
        server: DetectionResultServer,
        gf: GovernanceFilter,
        max_frames: int | None = None,
    ) -> None:
        self._server = server
        self._gf = gf
        self._max_frames = max_frames
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        count = 0
        for frame_results in self._server.frames():
            if not self._running:
                break
            if self._max_frames is not None and count >= self._max_frames:
                break
            try:
                self._gf.process_frame(frame_results)
            except Exception:
                log.exception("GovernanceFilter raised on frame %d", count)
            count += 1
        self._server.close()
        log.info("Governance service stopped after %d frames.", count)


def _open_supervisor(
    spec: str, *, fail_closed: bool = True
) -> tuple[SupervisorLink | None, MockR4Supervisor | None]:
    """Resolve --supervisor into a link and, in mock mode, the node behind it.

    Returns (None, None) for 'none', after warning: running the governance
    tier with nothing watching it is the configuration the oversight node
    exists to replace.
    """
    if spec == "none":
        log.warning(
            "No oversight node attached: command dispatch has no independent veto "
            "and audit rows are not witnessed off-host."
        )
        return None, None

    node: MockR4Supervisor | None
    if spec == "mock":
        node = MockR4Supervisor()
        node.start()
        channel = open(node.device, "rb+", buffering=0)  # noqa: SIM115
        log.info("Mock UNO R4 WiFi oversight node on pty %s", node.device)
    else:
        node = None
        channel = open(spec, "rb+", buffering=0)  # noqa: SIM115
        log.info("UNO R4 WiFi oversight node: %s", spec)

    link = SupervisorLink(channel, fail_closed=fail_closed)
    link.heartbeat(force=True)
    return link, node


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="VENTUNO Q governance service")
    parser.add_argument(
        "--listen", default=DEFAULT_LISTEN_ADDRESS,
        help="Bind address for UNO Q connection (default 0.0.0.0)",
    )
    parser.add_argument("--port", type=int, default=9100,
                        help="TCP port for UNO Q connection (default 9100)")
    parser.add_argument("--alvik", default="mock",
                        help="Alvik serial device (e.g. /dev/ttyACM0) or 'mock'")
    parser.add_argument("--supervisor", default="mock",
                        help="UNO R4 WiFi oversight device (e.g. /dev/ttyACM1), "
                             "'mock', or 'none' to run without an oversight tier")
    parser.add_argument("--oversight-optional", action="store_true",
                        help="Do not treat a lost oversight link as an override. "
                             "Bench use only: the default fails closed.")
    parser.add_argument("--db", default=":memory:",
                        help="Audit log SQLite path (default :memory:)")
    parser.add_argument("--threshold", type=float, default=0.70,
                        help="Linux-side confidence gate (default 0.70)")
    args = parser.parse_args(argv)

    audit_logger = AuditLogger(args.db)
    session_id = audit_logger.open_session()
    log.info("Audit session: %s  db: %s", session_id, args.db)

    if args.alvik == "mock":
        mock = MockSTM32H5()
        mock.start()
        channel = open(mock.device, "rb+", buffering=0)  # noqa: SIM115
        log.info("Mock STM32H5 on pty %s", mock.device)
    else:
        channel = open(args.alvik, "rb+", buffering=0)  # noqa: SIM115
        log.info("Alvik serial: %s", args.alvik)

    supervisor, mock_supervisor = _open_supervisor(
        args.supervisor, fail_closed=not args.oversight_optional
    )

    gf = GovernanceFilter(
        logger=audit_logger,
        session_id=session_id,
        channel=channel,
        confidence_threshold=args.threshold,
        command_map=DEFAULT_COMMAND_MAP,
        supervisor=supervisor,
    )

    server = DetectionResultServer(host=args.listen, port=args.port)
    server.start()
    log.info("Listening for UNO Q on %s:%d", args.listen, args.port)

    service = GovernanceService(server=server, gf=gf)

    def _shutdown(sig: int, frame: object) -> None:
        log.info("Shutting down (signal %d).", sig)
        service.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    service.run()
    channel.close()
    if supervisor is not None:
        supervisor.close()
    if mock_supervisor is not None:
        mock_supervisor.stop()
    if args.alvik == "mock":
        mock.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
