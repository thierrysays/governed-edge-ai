# Build Log

Running record of decisions, discoveries, and blockers for the Glossolalie Advisory case study and the Réseau Daubigny presentation.

---

## 2026-08-05 — Step 1: Audit logger and QA harness

**What was built.**

`audit-service/logger.py` — Python context-manager class wrapping the SQLite schema defined on day one. Key design decisions:

- **WAL mode + `synchronous=NORMAL`**: Write-Ahead Logging gives concurrent readers (dashboard) safe access without blocking the write path; `NORMAL` durability is sufficient for the governance use case (data survives crashes, not power loss to storage — a hardware-level concern handled by the dedicated NVMe SSD and UPS).
- **`log_event()` returns the row ID synchronously**: This is a protocol-level requirement, not just a convenience. The IPC specification (`docs/ipc-protocol.md`) requires the `audit_ref` field of every `COMMAND_REQUEST` frame to carry a confirmed log row ID. If logging fails, no frame is sent. This is the "log before act" governance constraint expressed in code.
- **`update_stm32_ack()` is idempotent**: `stm32_ack` starts NULL, is set once on ACK/REJECT receipt, and cannot be overwritten. Prevents a late-arriving duplicate ACK from flipping a confirmed record.
- **`flag_event()` is one-way**: `flag` can move 0→1 but never back. Flags are governance annotations, not editable metadata.
- **No UPDATE/DELETE on `audit_log` except `flag` and `stm32_ack`**: The service layer enforces the append-only constraint the schema cannot express alone.

**QA harness established as a canonical rule**: every module in this repo must ship with `pytest` unit tests, smoke tests, ruff lint, and `--cov-fail-under=90`. The root `Makefile` provides `make qa` as the CI gate. `audit-service` achieves 100% coverage (36 tests).

**Insight for article.** The audit log is not an afterthought bolted on after the AI is built — it is the primary governance artefact. The schema predates any inference code. The `audit_ref` field in the IPC protocol was designed specifically to create a tamper-evident ordering: you cannot send a command without a corresponding log entry that predates it, and the STM32H5 rejects frames with `audit_ref == 0`. This is ISO 42001 §9.1 expressed as a hardware-enforced protocol invariant.

---

## 2026-08-05 — Step 2: IPC frame codec

**What was built.**

`linux-stack/ipc/codec.py` — pure-Python, transport-agnostic implementation of the binary IPC protocol defined in `docs/ipc-protocol.md`.

Key components:

- **`crc16_ccitt(data)`**: CRC-16/CCITT (IBM-3740 variant, poly=0x1021, init=0xFFFF, no reflection). Verified against the known test vector `crc16_ccitt(b"123456789") == 0x29B1`. Protects every frame against corruption on the UART link.
- **Frozen dataclasses for all 8 message types**: `CommandRequest`, `CommandAck`, `CommandReject`, `HaltNotify`, `Heartbeat`, `HeartbeatAck`, `StatusQuery`, `StatusResponse`. Immutable — a received frame cannot be accidentally mutated after decode.
- **`encode(msg) → bytes`**: Packs header (magic 0xA5 + type byte + payload length as uint16 LE) + payload (struct-packed per message type) + CRC. All multi-byte fields are little-endian per spec.
- **`decode(frame) → Message`**: Validates magic, length, and CRC before dispatching to per-type unpackers. Raises `FrameError` (a `ValueError` subclass) on any structural violation. Enum values are validated at decode time — an unknown `action_type` or `reject_reason` raises `FrameError` before the caller ever sees it.
- **`FrameParser`**: Incremental stream parser for UART input. Accepts arbitrary byte chunks via `feed()`; finds magic bytes, buffers partial frames, emits decoded messages via `pop_messages()`. Garbage bytes before the magic byte are silently discarded per spec. Decode errors are logged to `parser.errors` rather than raising, so a single corrupt frame does not abort the stream.

**Frame sizes by message type** (header 4 bytes + payload + CRC 2 bytes):

| Message | Payload | Total |
|---|---|---|
| HEARTBEAT / HEARTBEAT_ACK / STATUS_QUERY | 0 | 6 |
| COMMAND_ACK / COMMAND_REJECT / HALT_NOTIFY | 9 | 15 |
| STATUS_RESPONSE | 14 | 20 |
| COMMAND_REQUEST | 20 | 26 |

**QA**: 52 tests (47 unit + 5 smoke), 98.6% coverage. The three uncovered lines are the `raise` at the end of `_unpack` (unreachable after the `_require` guards) and two branches inside the FrameParser error-recovery path that require a malformed-then-valid frame sequence not covered by current tests — noted for a future regression test.

**Insight for article.** The `FrameParser` is the safety-critical component most people overlook in serial protocol design. UART streams do not have message boundaries — bytes arrive in whatever chunks the OS DMA buffer happened to fill. A naive implementation that calls `decode()` on each `read()` result will fail on the first partial frame. The incremental parser buffers until a complete frame is available, seeks the magic byte on error recovery, and never blocks. This is the difference between a demonstrator that works on a bench and one that works reliably.

---

## 2026-08-05 — Repository initialised

**Context.** Board obtained via Arduino 21st-anniversary VENTUNO Q giveaway (contest entry submitted August 2026). Availability listed as Q2 2026; hardware not yet in hand at time of writing.

**Decisions taken today.**

- Repository name: `governed-edge-ai`. Hardware-agnostic name survives board naming changes; `ventuno-q-gov` was the runner-up.
- Licence: Apache 2.0 for code (explicit patent non-assertion clause, appropriate for enterprise-adjacent open source); CERN OHL-P v2 to be added when hardware design files are committed; CC BY 4.0 for documentation.
- Initial folder structure follows the layout from the project brief: `linux-stack/`, `rt-control/`, `audit-service/`, `dashboard/`, `docs/`.
- Audit log schema drafted in `audit-service/schema.sql`. Append-only semantics enforced at the service layer; schema includes a session registry table for per-power-cycle traceability.

**Open items blocking progress.**

- Official VENTUNO Q pinout not yet published; GPIO and MIPI-CSI connector assumptions are provisional throughout the codebase.
- Robotic arm model TBD; order deferred until budget vs degrees-of-freedom trade-off is resolved.
- Board power draw under sustained NPU load unknown; PSU wattage not finalised.

**Next steps.**

1. Monitor Arduino forums and official channels for pinout publication.
2. Draft IPC protocol specification between Linux and STM32H5 (see open governance questions in `docs/governance-mapping.md`).
3. Begin Python skeleton for the audit logger once pinout is confirmed.
4. Open GitHub Discussions or Issues for community input on arm model selection.
