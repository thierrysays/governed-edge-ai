# Build Log

Running record of decisions, discoveries, and blockers for the Glossolalie Advisory case study.

---

## 2026-08-05: Step 6: Governance filter (perception → audit → IPC dispatch)

**What was built.**

`linux-stack/governance/filter.py`: `GovernanceFilter`: the central safety gate that bridges the perception pipeline to the audit logger and IPC command dispatch.

Key design decisions:

- **Log-before-act as the primary constraint**: `process_frame()` calls `logger.log_event()` and receives a confirmed SQLite row ID (`audit_ref`) before constructing any `CommandRequest` frame. If logging fails, the exception propagates and no frame is sent. The audit record always precedes the command, never the reverse.
- **One command per frame with full audit trail**: Every detection in a frame is logged, regardless of whether a command is issued. The highest-confidence detection above the threshold gets `command_sent=True`; all others get `command_sent=False`. Suppressed detections remain in the audit log as forensic evidence of what the system saw and chose not to act on.
- **Dual-layer confidence gate**: The Linux filter (`confidence_threshold=0.70`) is the first gate. The STM32H5 mock peer applies an independent second gate at the same threshold. A detection that passes the Linux gate may still be rejected by the MCU (e.g. `0.70` in float64 encodes to slightly below `0.70` in float32; the dual gate catches this). This defence-in-depth means neither layer trusts the other's filtering decision.
- **Safety-conservative label mapping**: `DEFAULT_COMMAND_MAP` maps detection labels to `(ActionType, action_param)` pairs. Unknown labels fall through to `_DEFAULT_ACTION = (ActionType.HALT, 0)`. The system never ignores an unknown detection; it always halts. This is the correct safety-conservative default for an undefined input.
- **ACK/REJECT tracking via `_read_response()`**: After writing a `CommandRequest` frame, the filter polls the channel using `select.select` with a configurable timeout. `CommandAck` → `True` (MCU accepted); `CommandReject` → `False` (MCU rejected); timeout → `None` (stm32_ack left NULL in the audit log). Unrelated messages (HeartbeatAck, HaltNotify) are silently passed through the FrameParser for any higher-level consumer.
- **No cross-package imports in production code**: `filter.py` imports `AuditEvent` and `AuditLogger` from `audit-service/logger.py`. The caller (launch script or test conftest) is responsible for adding `audit-service/` to `sys.path`. This avoids both embedding sys.path manipulation in library code and introducing a heavyweight adapter layer.

**Runtime dependency**: `audit-service/logger.py` must be on `sys.path` at import time. Tests satisfy this via `linux-stack/tests/conftest.py` (updated in this step). Production satisfies this via `PYTHONPATH=../audit-service` in the launch environment.

`linux-stack/tests/test_governance.py`: 36 unit tests across 7 test classes: `TestEmptyFrame`, `TestConfidenceGate`, `TestCommandMapping`, `TestMultiDetectionFrame`, `TestLogBeforeAct`, `TestRejectPaths`, `TestTimeout`.

`linux-stack/tests/test_smoke_governance.py`: 7 `@pytest.mark.smoke` tests covering: import, full accept flow (object detection → HALT → ACK), NullPipeline no-op, low-confidence suppression, kill-switch rejection, three-backend composite frame, and log-before-act audit_ref validity.

**QA results**: 184 tests total (linux-stack), 97% coverage across governance + ipc + perception, ruff clean, mypy clean.

**Insight for article.** The governance filter is the component that makes the governance architecture visible. Every detection the perception pipeline produces becomes a record in the append-only audit log. Every command the system issues is linked to that record via `audit_ref`. Every response from the STM32H5 updates that record. The result is a complete, tamper-evident chain of custody: you can reconstruct exactly what the system saw, what it decided, whether it sent a command, and whether the hardware executed it. This is ISO 42001 §9.1 (monitoring and measurement) implemented as a code invariant, not a process requirement. The process cannot break it because the code enforces it.

---

## 2026-08-05: Step 5: Perception pipeline interface definitions

**What was built.**

`linux-stack/perception/base.py`: abstract interface definitions for the AI inference layer.

Key design decisions:

- **`DetectionResult` as a frozen dataclass**: The governance layer reads detections but must never mutate them. Frozen prevents any accidental field mutation after the backend produces the result: a `FrozenInstanceError` is raised rather than silently corrupting a detection mid-pipeline.
- **`confidence` in [0.0, 1.0] with float32 clamp**: The IPC codec encodes confidence as IEEE 754 float32. A Python `float64` value of exactly `1.0` encodes safely, but values computed by models can slightly exceed 1.0 due to softmax numerical noise. `__post_init__` clamps at `_CONFIDENCE_MAX = 0.9999`: this is a transport concern, not a model quality concern, and clamping silently is correct (raising would crash the perception loop on a trivially valid result).
- **`PerceptionPipeline` as an ABC with `run(frame) → list[DetectionResult]`**: The governance layer depends on this interface, not any specific backend. YOLO-X, MediaPipe, and PoseNet all subclass it; the governance filter is backend-agnostic. `run()` must return `[]` on inference error: never raise: to prevent a single corrupt frame from halting the safety loop.
- **`_stamp()` helper on PerceptionPipeline**: Backends call `self._stamp(result)` to populate `backend` without the caller needing to know the backend name. This keeps backend identity in the right layer.
- **Stub backends for offline development**: `StubObjectDetector`, `StubGestureRecognizer`, `StubPoseEstimator` return realistic detections without model weights, camera, or NPU. `NullPipeline` returns `[]` and exercises the "no detections → no command" governance path. All four are drop-in substitutes for their production equivalents.

**Production backend mapping** (not yet implemented, gated on VENTUNO Q pinout + NPU SDK):
- `StubObjectDetector` → YOLO-X on the Arduino NPU, watching the workspace via MIPI-CSI camera
- `StubGestureRecognizer` → MediaPipe Hands on CPU (model small enough to run off-NPU)
- `StubPoseEstimator` → MoveNet Lightning on NPU, proximity breach detection

`linux-stack/tests/test_perception.py`: 46 unit tests covering DetectionResult (construction, validation, frozen semantics, confidence clamping, `passes_threshold`) and all four stub backends.

`linux-stack/tests/test_smoke_perception.py`: 5 `@pytest.mark.smoke` tests covering the full perception→governance path: import, all stubs produce valid results, null pipeline suppresses commands, immutability enforcement, confidence gate filtering.

**QA results**: 141 tests total (linux-stack), 97.8% coverage, ruff clean.

**Insight for article.** The perception interface is the most important architectural decision in the AI stack: not because inference is hard, but because governance requires that inference be *replaceable*. The `PerceptionPipeline` ABC means the governance code (audit logging, IPC dispatch, confidence filtering) never imports a model name. Swapping YOLO-X for a different detector requires only a new subclass, zero changes to the governance layer. This is the Dependency Inversion Principle applied to safety: the high-level governance policy should not depend on the low-level inference detail. In automotive functional safety (ISO 26262), this is called "freedom from interference": one subsystem's failure mode cannot propagate into another.

---

## 2026-08-05: Step 4: Mock STM32H5 peer

**What was built.**

`linux-stack/ipc/mock_peer.py`: a complete software simulation of the STM32H5 real-time co-processor, exposing a pseudo-terminal (pty) slave endpoint that any serial-port client can connect to exactly as it would connect to a real UART.

Key design decisions:

- **Real pty pair, not a socket or pipe**: `os.openpty()` allocates a kernel pty pair. The slave end (`/dev/pts/N`) is a real tty node; the mock reads and writes through the master end. This means the Linux AI stack can use its actual serial-port driver (`pyserial` or direct `open()`) against the mock; no stub code in production paths.
- **`tty.setraw()` on the slave**: The pty line discipline applies Unix terminal semantics by default (0x0A → 0x0D 0x0A translation, echo, etc.). `setraw()` disables all of this, making the channel a transparent binary pipe: the same configuration a production serial port would use.
- **State machine enforced under a single lock**: `ARMED → BUSY → ARMED` (accepted command), `ARMED/BUSY → HALTED` (kill switch or watchdog), `any → FAULT` (explicit injection). All state transitions and stats mutations hold `self._lock`; the response frame is written *outside* the lock to prevent holding it across a blocking `os.write()`.
- **Reader thread + watchdog as separate threads**: The reader loop uses `select.select` with a 50ms poll interval so it can check `self._running` without blocking indefinitely. The watchdog is a `threading.Timer` that arms on construction and re-arms on every heartbeat: conceptually identical to the hardware timer the real STM32H5 uses.
- **Reject gate priority order**: (1) `audit_ref == 0` → `AUDIT_REF_ZERO`; (2) kill switch open → `KILL_SWITCH_ACTIVE`; (3) HALTED state → `WATCHDOG_TIMEOUT`; (4) FAULT state → `SYSTEM_FAULT`; (5) confidence below threshold → `CONFIDENCE_BELOW_THRESHOLD`. The audit-ref check is first and unconditional: no path bypasses it, regardless of system state.
- **Float32 boundary at confidence threshold**: The IPC codec encodes `confidence` as IEEE 754 float32. The value `0.70` encodes to `0.6999...` after float32 round-trip, which falls below the default `0.70` threshold and is correctly rejected. Tests use `0.75` (exactly representable in float32) to exercise the accept-at-threshold path.

`linux-stack/tests/test_mock_peer.py`: 35 unit tests across 8 test classes (TestHeartbeat, TestStatusQuery, TestCommandAccept, TestCommandReject, TestKillSwitch, TestWatchdog, TestStats, TestLifecycle). All exercise a real pty pair via `select.select` with configurable timeouts: no mocking of the pty layer.

`linux-stack/tests/test_smoke_mock.py`: 5 `@pytest.mark.smoke` tests covering the core governance paths: liveness (heartbeat roundtrip), log-before-act enforcement (audit_ref=0 rejection), full command lifecycle (heartbeat → command → ACK → status query), and kill-switch halt-and-reject.

**QA results**: 92 tests total (linux-stack), 97.5% coverage, ruff clean.

**Insight for article.** The mock peer is the offline development enabler for the entire governance stack. Without it, every integration test requires a physical STM32H5 board connected via UART: which means tests can only run on one specific machine, CI is impossible, and the hardware becomes the bottleneck. With the mock, the Linux AI stack, the audit logger, and the IPC codec can all be developed, tested, and iterated on independently of the hardware. The governance invariants (log-before-act, kill-switch supremacy, watchdog) are exercised in milliseconds on any machine. This is the same pattern the automotive industry uses: HIL (Hardware-in-the-Loop) testing for firmware, SIL (Software-in-the-Loop) for application code. The mock peer is the SIL simulator for the STM32H5.

---

## 2026-08-05: Step 1: Audit logger and QA harness

**What was built.**

`audit-service/logger.py`: Python context-manager class wrapping the SQLite schema defined on day one. Key design decisions:

- **WAL mode + `synchronous=NORMAL`**: Write-Ahead Logging gives concurrent readers (dashboard) safe access without blocking the write path; `NORMAL` durability is sufficient for the governance use case (data survives crashes, not power loss to storage: a hardware-level concern handled by the dedicated NVMe SSD and UPS).
- **`log_event()` returns the row ID synchronously**: This is a protocol-level requirement, not just a convenience. The IPC specification (`docs/ipc-protocol.md`) requires the `audit_ref` field of every `COMMAND_REQUEST` frame to carry a confirmed log row ID. If logging fails, no frame is sent. This is the "log before act" governance constraint expressed in code.
- **`update_stm32_ack()` is idempotent**: `stm32_ack` starts NULL, is set once on ACK/REJECT receipt, and cannot be overwritten. Prevents a late-arriving duplicate ACK from flipping a confirmed record.
- **`flag_event()` is one-way**: `flag` can move 0→1 but never back. Flags are governance annotations, not editable metadata.
- **No UPDATE/DELETE on `audit_log` except `flag` and `stm32_ack`**: The service layer enforces the append-only constraint the schema cannot express alone.

**QA harness established as a canonical rule**: every module in this repo must ship with `pytest` unit tests, smoke tests, ruff lint, and `--cov-fail-under=90`. The root `Makefile` provides `make qa` as the CI gate. `audit-service` achieves 100% coverage (36 tests).

**Insight for article.** The audit log is not an afterthought bolted on after the AI is built: it is the primary governance artefact. The schema predates any inference code. The `audit_ref` field in the IPC protocol was designed specifically to create a tamper-evident ordering: you cannot send a command without a corresponding log entry that predates it, and the STM32H5 rejects frames with `audit_ref == 0`. This is ISO 42001 §9.1 expressed as a hardware-enforced protocol invariant.

---

## 2026-08-05: Step 2: IPC frame codec

**What was built.**

`linux-stack/ipc/codec.py`: pure-Python, transport-agnostic implementation of the binary IPC protocol defined in `docs/ipc-protocol.md`.

Key components:

- **`crc16_ccitt(data)`**: CRC-16/CCITT (IBM-3740 variant, poly=0x1021, init=0xFFFF, no reflection). Verified against the known test vector `crc16_ccitt(b"123456789") == 0x29B1`. Protects every frame against corruption on the UART link.
- **Frozen dataclasses for all 8 message types**: `CommandRequest`, `CommandAck`, `CommandReject`, `HaltNotify`, `Heartbeat`, `HeartbeatAck`, `StatusQuery`, `StatusResponse`. Immutable: a received frame cannot be accidentally mutated after decode.
- **`encode(msg) → bytes`**: Packs header (magic 0xA5 + type byte + payload length as uint16 LE) + payload (struct-packed per message type) + CRC. All multi-byte fields are little-endian per spec.
- **`decode(frame) → Message`**: Validates magic, length, and CRC before dispatching to per-type unpackers. Raises `FrameError` (a `ValueError` subclass) on any structural violation. Enum values are validated at decode time: an unknown `action_type` or `reject_reason` raises `FrameError` before the caller ever sees it.
- **`FrameParser`**: Incremental stream parser for UART input. Accepts arbitrary byte chunks via `feed()`; finds magic bytes, buffers partial frames, emits decoded messages via `pop_messages()`. Garbage bytes before the magic byte are silently discarded per spec. Decode errors are logged to `parser.errors` rather than raising, so a single corrupt frame does not abort the stream.

**Frame sizes by message type** (header 4 bytes + payload + CRC 2 bytes):

| Message | Payload | Total |
|---|---|---|
| HEARTBEAT / HEARTBEAT_ACK / STATUS_QUERY | 0 | 6 |
| COMMAND_ACK / COMMAND_REJECT / HALT_NOTIFY | 9 | 15 |
| STATUS_RESPONSE | 14 | 20 |
| COMMAND_REQUEST | 20 | 26 |

**QA**: 52 tests (47 unit + 5 smoke), 98.6% coverage. The three uncovered lines are the `raise` at the end of `_unpack` (unreachable after the `_require` guards) and two branches inside the FrameParser error-recovery path that require a malformed-then-valid frame sequence not covered by current tests: noted for a future regression test.

**Insight for article.** The `FrameParser` is the safety-critical component most people overlook in serial protocol design. UART streams do not have message boundaries: bytes arrive in whatever chunks the OS DMA buffer happened to fill. A naive implementation that calls `decode()` on each `read()` result will fail on the first partial frame. The incremental parser buffers until a complete frame is available, seeks the magic byte on error recovery, and never blocks. This is the difference between a demonstrator that works on a bench and one that works reliably.

---

## 2026-08-05: Repository initialised

**Context.** Board obtained via Arduino 21st-anniversary VENTUNO Q giveaway (contest entry submitted August 2026). Availability listed as Q2 2026; hardware not yet in hand at time of writing.

**Decisions taken today.**

- Repository name: `governed-edge-ai`. Hardware-agnostic name survives board naming changes; `ventuno-q-gov` was the runner-up.
- Licence: Apache 2.0 for code (explicit patent non-assertion clause, appropriate for enterprise-adjacent open source); CERN OHL-P v2 to be added when hardware design files are committed; CC BY 4.0 for documentation.
- Initial folder structure follows the layout from the project brief: `linux-stack/`, `rt-control/`, `audit-service/`, `dashboard/`, `docs/`.
- Audit log schema drafted in `audit-service/schema.sql`. Append-only semantics enforced at the service layer; schema includes a session registry table for per-power-cycle traceability.

---

## 2026-08-05: Step 3: Dashboard backend (FastAPI)

**What was built.**

`audit-service/dashboard/app.py`: FastAPI web service exposing the audit log over the local network (Wi-Fi 6). Five routes:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe; returns DB path |
| GET | `/sessions` | All sessions, ordered newest-first |
| GET | `/events` | Filtered event log: `session_id`, `actor`, `flagged`, `limit`, `offset` |
| POST | `/events/{id}/flag` | Human-reviewer annotation (the only write path in the dashboard) |
| GET | `/query?q=` | Natural-language query stub; wired to LLM in Step 5 |

Key design decisions:

- **Dependency injection for `get_db`**: each request gets its own SQLite connection, opened fresh and closed on response completion. This makes the DB path overridable in tests without any global state mutation: the override just replaces the dependency with a lambda pointing to the temp DB.
- **WAL mode on every connection**: the dashboard opens connections concurrently with the logger. SQLite WAL allows multiple simultaneous readers alongside the logger's single writer: no locking needed.
- **Actor validation at the HTTP layer**: the `actor` query parameter uses FastAPI's `pattern="^(ai|human_override)$"`: invalid values return a 422 before they reach the DB. This prevents SQL injection via the filter parameter and enforces the schema's CHECK constraint at the API boundary.
- **Boolean coercion**: SQLite stores booleans as integers (0/1). The `_coerce_event()` helper converts them to Python `bool` before serialisation, so the JSON response contains `true`/`false`, not `0`/`1`. This is the difference between an API that's self-documenting and one that confuses every consumer.
- **LAN-only CORS**: CORS is configured via `ALLOWED_ORIGINS` environment variable defaulting to `"*"` for development. In deployment on the VENTUNO Q, this will be locked to the local subnet. No outbound calls; no telemetry.

**Testing approach**: 36 new tests using `TestClient` with a dependency override. The seeded fixture uses `AuditLogger` to write test data: the same write path as production, so the test exercises the full stack (logger → SQLite → dashboard API → JSON response). Coverage: 96% across logger + dashboard.

**Insight for article.** The dependency injection pattern is the key to testable service code. The production DB path is hardcoded as a default, but `app.dependency_overrides[get_db] = override` replaces it without touching any global state. The test creates a real DB, writes real events via `AuditLogger`, and then queries them through the full HTTP stack: no mocking of the DB layer. This means the tests are also integration tests for the logger↔dashboard handoff.

**Open items blocking progress.**

- Official VENTUNO Q pinout not yet published; GPIO and MIPI-CSI connector assumptions are provisional throughout the codebase.
- Robotic arm model TBD; order deferred until budget vs degrees-of-freedom trade-off is resolved.
- Board power draw under sustained NPU load unknown; PSU wattage not finalised.

**Next steps.**

1. Monitor Arduino forums and official channels for pinout publication.
2. Draft IPC protocol specification between Linux and STM32H5 (see open governance questions in `docs/governance-mapping.md`).
3. Begin Python skeleton for the audit logger once pinout is confirmed.
4. Open GitHub Discussions or Issues for community input on arm model selection.
