# Governed Edge AI: Cowork Session Summary

**Project:** governed-edge-ai Physical AI Demonstrator
**Repository:** github.com/thierrysays/governed-edge-ai (public, main branch)
**Session date:** 5 August 2026
**Status:** Complete: all six build steps shipped, main branch live, content deliverables produced

---

## What This Project Is

A working demonstrator of governance-first Physical AI on the Arduino VENTUNO Q: a dual-brain board combining a Qualcomm Dragonwing IQ8 NPU (40 TOPS, Ubuntu Linux) with an STM32H5 real-time co-processor (Zephyr OS + Arduino Core).

The core thesis: for physical AI systems, safety invariants must be enforced at the hardware and protocol level, not only in policy documents or governance frameworks. The demonstrator proves this with a production-quality implementation: no actuator command can execute without a prior audit log entry, enforced at the IPC protocol layer.

Stack: Python 3.11, SQLite WAL-mode, binary UART IPC with CRC-16/CCITT, pytest, ruff, mypy, bandit, pip-audit. 184 tests, 97% coverage, hardware-free test suite via a pty-based co-processor mock.

---

## Build Sequence (Six Commits to Main)

### Step 1: Audit Logger (`audit-service/logger.py`)

Append-only SQLite audit service in WAL mode. The central building block everything else depends on.

Key design:
- `open_session(board_serial)` → returns a session UUID
- `log_event(AuditEvent)` → writes and returns the SQLite rowid (the `audit_ref`; always ≥ 1)
- `update_stm32_ack(audit_ref, ack)` → idempotent one-time update after MCU responds
- `flag_event(audit_ref, reason)` → one-way flag for forensic marking; cannot be unflagged
- Schema: `sessions` table + `audit_log` table; `stm32_ack` column is NULL until the MCU responds

QA: 148 tests across logger, dashboard, and smoke suites. Full coverage.

---

### Step 2: IPC Codec (`linux-stack/ipc/codec.py`)

Binary protocol between the Linux NPU and the STM32H5 co-processor. Eight message types:

| Message | Direction | Purpose |
|---|---|---|
| `CommandRequest` | Linux → MCU | Governance-approved action |
| `CommandAck` | MCU → Linux | Accepted and executed |
| `CommandReject` | MCU → Linux | Rejected (kill switch / confidence gate / audit_ref=0) |
| `Heartbeat` | Linux → MCU | Watchdog keepalive |
| `HeartbeatAck` | MCU → Linux | Watchdog acknowledged |
| `HaltNotify` | MCU → Linux | Emergency halt from MCU |
| `StatusQuery` | Linux → MCU | Request MCU state |
| `StatusResponse` | MCU → Linux | Current MCU state |

CRC-16/CCITT on every frame. `FrameParser` handles incremental stream input across multiple `read()` calls.

Critical design decision: `audit_ref=0` is a reserved sentinel. The STM32H5 rejects it unconditionally, making log-before-act enforceable at the wire level.

---

### Step 3: Dashboard (`audit-service/dashboard/`)

Flask/SQLAlchemy read-only dashboard over the audit log. Intentionally read-only: the governance filter writes via `AuditLogger`; the dashboard only reads.

Views: session list with event counts, chronological event stream (detection label / confidence / command / sent/suppressed / ACK/REJECT), per-session suppression rate.

---

### Step 4: Mock STM32H5 Peer (`linux-stack/ipc/mock_peer.py`)

Unix pty-based hardware simulator. Behaves identically to the real co-processor: decodes `CommandRequest` frames, enforces its own confidence gate in float32, manages state machine (ARMED → BUSY → HALTED/FAULT), responds with `CommandAck` or `CommandReject`.

```python
with MockSTM32H5(watchdog_ms=10_000.0) as peer:
    ch = open(peer.device, "rb+", buffering=0)
    # ch is now a binary r/w channel identical to a real UART
```

Key behaviours:
- Dual confidence gate at float32 precision: `confidence=0.70` in float64 encodes to slightly below 0.70 in float32 and is rejected by the mock even when Linux passed it
- `peer.trigger_kill_switch()` puts MCU into HALTED state; subsequent commands receive `CommandReject`
- Watchdog timer fires if `Heartbeat` frames stop arriving

Enables the entire test suite to run without hardware.

---

### Step 5: Perception Pipeline (`linux-stack/perception/`)

Minimal typed detection layer. ABC enforces `run(frame) -> list[DetectionResult]` across all backends.

```python
@dataclass(frozen=True)
class DetectionResult:
    detection_type: str    # "object" | "gesture" | "pose"
    label: str
    confidence: float      # clamped to [0.0, 1.0]

    def passes_threshold(self, threshold: float) -> bool: ...
```

Three stub backends ship with the demonstrator:
- `StubObjectDetector(confidence)` → `person` detection
- `StubGestureRecognizer(confidence)` → `thumbs_up` detection
- `StubPoseEstimator(confidence)` → `proximity_breach` detection
- `NullPipeline()` → empty list (no detections)

Any of these can be swapped for a real backend (YOLO, MediaPipe, a custom NPU model) without touching the governance layer.

---

### Step 6: Governance Filter (`linux-stack/governance/filter.py`)

The safety gate. Sits between the perception pipeline and the IPC channel. Enforces six non-negotiable invariants:

1. **Log-before-act**: `audit_ref` is obtained from `logger.log_event()` before any `CommandRequest` frame is transmitted. If `log_event()` raises, the exception propagates and no frame is sent.
2. **No log, no command**: enforced structurally; send happens inside the `if should_send:` block that follows the log call.
3. **Confidence gate (Linux side)**: detections below threshold are logged with `command_sent=False`. Suppression is on record.
4. **One command per frame**: highest-confidence detection that clears the threshold is selected. All others logged as suppressed.
5. **Dual-layer gate**: Linux gate and STM32H5 gate operate independently (defence-in-depth).
6. **ACK/REJECT tracking**: `update_stm32_ack()` called exactly once per transmitted command. Timeout leaves `stm32_ack` NULL.

Default command map (safety-conservative; unknown labels default to HALT):

| Label | Command |
|---|---|
| `person`, `robot_part`, `tool` | HALT |
| `stop` (gesture) | HALT |
| `thumbs_up` (gesture) | GRIPPER_OPEN |
| `thumbs_down` (gesture) | GRIPPER_CLOSE |
| `proximity_breach` (pose) | HALT |
| *(anything else)* | HALT |

36 unit tests + 7 smoke tests covering: empty frame, confidence gate (both sides of threshold), all default command mappings, custom maps, multi-detection frame sorting, log-before-act integrity, kill-switch rejection, float32 rounding rejection, and the timeout path (tested via OS pipe: write-only channel that never returns a response).

---

## QA Baseline (as of final commit `a2672f3`)

| Check | Result |
|---|---|
| Tests | 184 passed |
| Coverage | 97% |
| `ruff check` | All checks passed |
| `mypy` | No issues found in 8 source files |
| `bandit` | No issues |
| `pip-audit` | No known CVEs |

Run the full suite: `make qa` (lint + typecheck + security + test)
Run smoke tests only: `make smoke`

---

## Repository Structure

```
governed-edge-ai/
├── Makefile                        # make smoke | test | lint | typecheck | security | qa
├── docs/
│   └── build-log.md                # Step-by-step design decisions and QA results
├── audit-service/
│   ├── logger.py                   # AuditLogger, AuditEvent, session management
│   ├── dashboard/                  # Flask read-only audit dashboard
│   │   ├── app.py
│   │   └── models.py
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── tests/                      # 148 tests
└── linux-stack/
    ├── ipc/
    │   ├── codec.py                 # Binary IPC protocol, 8 message types, CRC-16/CCITT
    │   └── mock_peer.py             # Pty-based STM32H5 simulator
    ├── perception/
    │   ├── base.py                  # DetectionResult dataclass, PerceptionPipeline ABC
    │   └── backends.py              # Stub backends + NullPipeline
    ├── governance/
    │   └── filter.py                # GovernanceFilter, DEFAULT_COMMAND_MAP
    ├── requirements.txt
    ├── pyproject.toml
    └── tests/                       # 36 unit tests + 7 smoke tests for governance
        ├── conftest.py
        ├── test_governance.py
        ├── test_smoke_governance.py
        ├── test_ipc_codec.py
        ├── test_mock_peer.py
        ├── test_perception.py
        └── test_smoke_*.py
```

---

## Content Deliverables Produced

### 1. LinkedIn Pulse Article

**Title:** "When AI Controls Physical Systems: Governance Must Be a Hardware Invariant, Not a Policy Document"
**Pillar:** B: AI Governance, Regulation & the Infrastructure It Forces
**Format:** Full C-suite editorial format (7 mandatory sections), ~1,100 words
**Files:** `s-physical-ai-governance.docx` + `s-physical-ai-governance.pdf`
**Publish date:** Thursday 7 August 2026 (next Thursday per editorial calendar)

Pull quote:
> *"ISO 42001 requires organisations to monitor and measure AI performance. It does not specify where in the system stack to place the monitor. For physical AI, that question is not optional: the wrong answer can injure someone."*

Sources (all open-access): IFR World Robotics 2025, OSHA severe injury data, EU AI Act (Regulation 2024/1689), ISO 42001:2023, NIST AI RMF 1.0, CEDAR-42001 (arxiv:2606.21276), Arduino VENTUNO Q, Digital Omnibus Package.

What This Article Doesn't Resolve (four genuine open tensions):
- Threshold calibration is an engineering judgment, not a governance standard
- The audit log is tamper-evident, not tamper-proof
- The STM32H5 firmware sits outside this governance framework
- ISO 42001 certification does not validate this architecture

Hashtags: `#PhysicalAI #AIGovernance #EUAIAct #EdgeAI #RoboticsSafety #ISO42001`

---

### 2. LinkedIn Companion Feed Post

~1,680 chars. Scroll-stopper hook: *"Most AI governance frameworks were built for software. Not for systems that move."*

Four `→` bullet takeaways. Link in first comment (to be added after article publishes). First-comment template pre-written with three key source citations and repo URL.

---

### 3. Technical Tutorial

**Title:** "Building Governance-First Physical AI: A Step-by-Step Architecture on the Arduino VENTUNO Q"
**Format:** Markdown, suitable for Glossolalie Advisory website, dev.to, or Substack
**File:** `technical-tutorial.md`

Structured around the six-commit build sequence. Each section includes code snippets, design rationale, and test patterns. Closes with a "What This Architecture Doesn't Solve Yet" section mirroring the honest posture of the Pulse article.

---

### 4. LinkedIn Arduino Outreach Post

Tags Massimo Banzi (co-founder/CTO, presented VENTUNO Q at Embedded World March 2026) and Fabio Violante (VP & GM, Arduino). Technically specific: cites the binary protocol, the dual-gate float32 edge case, and the 184-test count.

Invites hardware contribution programme / developer preview participation. Tone: technically credible, collegial, not promotional.

Includes posting notes: verify handles before tagging, attach test suite screenshot, post from personal profile, ensure repo is public (it is).

---

## Open Items / Next Steps

| Item | Notes |
|---|---|
| Publish Pulse article | Thursday 7 August 2026 |
| Post companion feed post | Immediately after article publishes; add article URL to first comment |
| Post Arduino outreach | Before or same day as article; creates social proof |
| Add tutorial to glossolalie.pro | Self-contained, not time-sensitive |
| Real hardware validation | STM32H5 firmware + Zephyr integration not yet written; governance stops at IPC boundary |
| Tamper-proof audit log | SQLite WAL is tamper-evident only; production needs signed entries or hardware-secured storage |
| Confidence threshold calibration | 0.70 is an engineering judgment; no published standard maps confidence to injury probability for HRC |
| GRIPPER_OPEN/GRIPPER_CLOSE firmware | ActionType enum defined; MCU-side actuation code not in scope for this session |

---

## Key Architectural Decisions (for future contributors)

**Why log-before-act is structural, not procedural:** `audit_ref` is a return value from `log_event()`. The send is inside an `if should_send:` block that follows the log call. There is no code path that sends without logging; the structure prevents it.

**Why the mock peer uses a pty, not a socket:** The real UART is opened as a file descriptor with `buffering=0`. The test fixtures open `peer.device` the same way. The test-to-production interface is identical.

**Why `audit_ref=0` is rejected at the wire level:** `log_event()` returns a SQLite rowid, which is always ≥ 1. A value of 0 indicates the frame was constructed without logging. The MCU's rejection of `audit_ref=0` makes this invariant enforceable even if the Linux-side code is patched to skip logging.

**Why the command map defaults to HALT for unknown labels:** Unknown inputs in a safety-critical system are conservatively treated as hazardous. The `_DEFAULT_ACTION = (ActionType.HALT, 0)` fallback ensures new labels from an updated model do not produce unexpected actuation until the map is explicitly updated.

**Why `stm32_ack` is nullable (NULL, not False) on timeout:** NULL means "no response received." False means "response received: rejected." The distinction is forensically meaningful. A NULL entry may indicate a network/serial fault; a False entry indicates the MCU made a deliberate decision.
