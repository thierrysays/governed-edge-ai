# IPC Protocol Specification

Version 0.3, 2026-08-19  
Status: implemented in `linux-stack/ipc/codec.py`, `alvik-firmware/ipc_codec.py` and `r4-supervisor/ipc_frame.cpp`

## Purpose

This document specifies the inter-processor communication (IPC) protocol used on two links:

| Link | Endpoints | Purpose |
|---|---|---|
| **Actuation** | VENTUNO Q (Linux / STM32H5) to Alvik (STM32F411) | Command dispatch and acknowledgement |
| **Oversight** | VENTUNO Q to UNO R4 WiFi | Liveness reporting, audit attestation, override assertion, latch relay requests and reports |

Both links share one frame format, one CRC and one parser. They differ in who may say what to whom, and that asymmetry is the governance content of this specification.

It is a governance artefact as much as a technical one: the protocol is the mechanism by which authority separation is enforced at the message layer.

On the actuation link, the Linux side may send command requests. The STM32 may accept or reject them. No path exists by which the Linux side can compel execution.

On the oversight link, the direction of authority reverses. Everything the VENTUNO Q sends is a report. Everything the R4 sends is an instruction. There is no `OVERRIDE_DENY`, and no message the governance tier can send that clears an override: releasing one is a physical act at the oversight node. A control its own subject can switch off is not a control.

## Transport

**Primary:** UART (serial), full-duplex.  
Baud rate: 921600 (provisional; confirm against STM32H5 UART peripheral limits once pinout is published).  
Hardware flow control: RTS/CTS if the pinout supports it; software flow control otherwise.

**Alternative:** Shared memory via MMIO if the board's inter-processor bus supports it. Evaluate once the VENTUNO Q technical reference manual is available. The frame format and message semantics defined here apply regardless of transport.

Both sides must apply the CA bundle at `/root/.ccr/ca-bundle.crt` if TLS is ever added to the transport. For the initial implementation over bare UART, frame integrity relies on CRC-16.

## Frame format

All multi-byte fields are little-endian.

```
Offset  Length  Field
------  ------  -----
0       1       Magic byte: 0xA5
1       1       Message type (uint8)
2       2       Payload length in bytes (uint16)
4       N       Payload (0 to 255 bytes)
4+N     2       CRC-16/CCITT of bytes 0 through 3+N inclusive
```

Maximum frame size: 261 bytes (4 header + 255 payload + 2 CRC).  
Minimum frame size: 6 bytes (4 header + 0 payload + 2 CRC).

A receiver that sees any byte other than 0xA5 at the start of a frame discards bytes until 0xA5 is found. A CRC mismatch causes the frame to be discarded; the STM32H5 replies with COMMAND_REJECT (reason: MALFORMED_FRAME) if a command request was being parsed.

## Message types

### Actuation link: Linux to STM32

| Type byte | Name            | Direction      |
|-----------|-----------------|----------------|
| 0x01      | COMMAND_REQUEST | Linux → STM32 |
| 0x10      | HEARTBEAT       | Linux → STM32 |
| 0x20      | STATUS_QUERY    | Linux → STM32 |

### Actuation link: STM32 to Linux

| Type byte | Name             | Direction       |
|-----------|------------------|-----------------|
| 0x81      | COMMAND_ACK      | STM32 → Linux |
| 0x82      | COMMAND_REJECT   | STM32 → Linux |
| 0x90      | HALT_NOTIFY      | STM32 → Linux |
| 0x11      | HEARTBEAT_ACK    | STM32 → Linux |
| 0x21      | STATUS_RESPONSE  | STM32 → Linux |

### Oversight link: VENTUNO Q to UNO R4 WiFi

| Type byte | Name                 | Direction         |
|-----------|----------------------|-------------------|
| 0x30      | SUPERVISOR_HEARTBEAT | VENTUNO Q → R4 |
| 0x31      | ATTEST_DIGEST        | VENTUNO Q → R4 |
| 0x32      | LATCH_REQUEST        | VENTUNO Q → R4 |

### Oversight link: UNO R4 WiFi to VENTUNO Q

| Type byte | Name             | Direction       |
|-----------|------------------|-----------------|
| 0xA0      | OVERRIDE_ASSERT  | R4 → VENTUNO Q |
| 0xA1      | OVERRIDE_CLEAR   | R4 → VENTUNO Q |
| 0xA2      | ATTEST_ACK       | R4 → VENTUNO Q |
| 0xA3      | LATCH_REPORT     | R4 → VENTUNO Q |

The R4 implements no decoder for COMMAND_REQUEST. It is not on the actuation path, and leaving those decoders out of `r4-supervisor/ipc_frame.cpp` reduces what that board can be talked into doing.

---

## Payload definitions

### COMMAND_REQUEST (0x01): 20 bytes

The Linux side must write the audit log entry and obtain a confirmed entry ID before constructing this frame. The `audit_ref` field carries that ID. A command request sent without a valid `audit_ref` is rejected.

| Offset | Length | Field         | Notes |
|--------|--------|---------------|-------|
| 0      | 8      | audit_ref     | uint64: audit log entry ID written before this frame |
| 8      | 4      | timestamp_us  | uint32: microseconds since session start |
| 12     | 1      | actor         | 0x01 = ai, 0x02 = human_override |
| 13     | 4      | confidence    | float32 (IEEE 754): 0.0 to 1.0 |
| 17     | 1      | action_type   | see action type table |
| 18     | 2      | action_param  | int16: action-specific parameter |

### COMMAND_ACK (0x81): 9 bytes

| Offset | Length | Field     | Notes |
|--------|--------|-----------|-------|
| 0      | 8      | audit_ref | uint64: echoed from the request |
| 8      | 1      | status    | 0x00 = queued, 0x01 = executing |

### COMMAND_REJECT (0x82): 9 bytes

| Offset | Length | Field     | Notes |
|--------|--------|-----------|-------|
| 0      | 8      | audit_ref | uint64: echoed from the request; 0 if frame was malformed |
| 8      | 1      | reason    | see reject reason table |

### HALT_NOTIFY (0x90): 9 bytes

Sent by the STM32H5 within 1 ms of any halt-triggering event, without waiting for a request.

| Offset | Length | Field        | Notes |
|--------|--------|--------------|-------|
| 0      | 8      | timestamp_us | uint64: microseconds since session start |
| 8      | 1      | trigger      | see halt trigger table |

### HEARTBEAT (0x10): 0 bytes payload

No payload. The Linux side sends this every 500 ms.

### HEARTBEAT_ACK (0x11): 0 bytes payload

No payload. Sent by the STM32H5 in response to each HEARTBEAT.

### STATUS_QUERY (0x20): 0 bytes payload

No payload.

### STATUS_RESPONSE (0x21): 14 bytes

| Offset | Length | Field              | Notes |
|--------|--------|--------------------|-------|
| 0      | 1      | system_state       | see system state table |
| 1      | 1      | kill_switch_gpio   | 0x00 = NC closed (normal), 0x01 = open (halt triggered) |
| 2      | 4      | commands_received  | uint32: since session start |
| 6      | 4      | commands_rejected  | uint32: since session start |
| 10     | 4      | commands_executed  | uint32: since session start |

---

## Oversight link payload definitions

### SUPERVISOR_HEARTBEAT (0x30): 17 bytes

Sent by the VENTUNO Q every 500 ms, and immediately after each audit row is published. It is a report, not a request, and draws no reply on a healthy link. Silence on the return path means the veto is not raised.

| Offset | Length | Field           | Notes |
|--------|--------|-----------------|-------|
| 0      | 8      | last_audit_ref  | uint64: highest audit_log rowid written so far |
| 8      | 1      | system_state    | see system state table |
| 9      | 4      | events_logged   | uint32: rows written this session |
| 13     | 4      | commands_sent   | uint32: CommandRequest frames transmitted this session |

The counters let the oversight node compare what the governance tier claims to have logged against the digests it has actually witnessed.

### ATTEST_DIGEST (0x31): 40 bytes

Sent once per logged audit row, before the corresponding CommandRequest frame reaches the actuation link. The independent witness therefore holds a digest covering the audit row before the actuator sees the command.

| Offset | Length | Field      | Notes |
|--------|--------|------------|-------|
| 0      | 8      | audit_ref  | uint64: rowid of the row that produced this head |
| 8      | 32     | digest     | SHA-256 chain head: `SHA-256(previous_head \|\| canonical(row))` |

The canonical row rendering is defined in `linux-stack/oversight/attestation.py`. It covers the columns fixed at the moment of the command decision, and deliberately excludes `stm32_ack` and `flag`, both written afterwards.

### OVERRIDE_ASSERT (0xA0): 9 bytes

Sent by the R4 the moment an override latches. Unsolicited.

| Offset | Length | Field        | Notes |
|--------|--------|--------------|-------|
| 0      | 8      | timestamp_us | uint64: microseconds since R4 boot |
| 8      | 1      | reason       | see override reason table |

### OVERRIDE_CLEAR (0xA1): 8 bytes

Sent when an operator releases a latched override at the board.

| Offset | Length | Field        | Notes |
|--------|--------|--------------|-------|
| 0      | 8      | timestamp_us | uint64 |

### ATTEST_ACK (0xA2): 9 bytes

The R4's live verdict on the last ATTEST_DIGEST.

| Offset | Length | Field     | Notes |
|--------|--------|-----------|-------|
| 0      | 8      | audit_ref | uint64: echoed from the digest frame |
| 8      | 1      | verdict   | see attestation verdict table |

### LATCH_REQUEST (0x32): 9 bytes

A **request**, not a command. The oversight node owns the latch relay and decides. A request to close while an override is latched is refused; a request to open is always honoured, because adding ways to stop is safe and adding ways to start is not.

| Offset | Length | Field     | Notes |
|--------|--------|-----------|-------|
| 0      | 8      | audit_ref | uint64: the audit row justifying the request, 0 if none |
| 8      | 1      | desired   | see latch position table |

### LATCH_REPORT (0xA3): 11 bytes

Sent on every request, on every commanded change, and whenever a poll finds the sources disagreeing.

| Offset | Length | Field       | Notes |
|--------|--------|-------------|-------|
| 0      | 1      | commanded   | what the arbiter last asked for |
| 1      | 1      | reported    | what the module's own MCU says |
| 2      | 1      | observed    | what the sense pair on the contact says |
| 3      | 4      | transitions | uint32: commanded state changes this session |
| 7      | 4      | mismatches  | uint32: polls where the sources disagreed |

Three positions rather than one, because they answer different questions. `reported` comes from the module's microcontroller and most likely echoes the last command it accepted rather than observing the contact. `observed` comes from an antivalent pair of opto-isolated sense channels on the contact and is the source of truth. Their disagreement is the finding: a failed relay, a broken sense channel, or a module misreporting its own state.

---

## Reference tables

### System state

| Value | Name    | Meaning |
|-------|---------|---------|
| 0x00  | ARMED   | Ready to receive and execute command requests |
| 0x01  | HALTED  | Kill switch active, watchdog expired, or halt commanded; no actuation |
| 0x02  | BUSY    | Executing a command; new requests are queued or rejected |
| 0x03  | FAULT   | Unrecoverable error; requires power cycle |

### Action types

| Value | Name          | action_param meaning |
|-------|---------------|----------------------|
| 0x00  | NONE          | Ignored |
| 0x01  | HALT          | Software-commanded halt; param ignored |
| 0x10  | MOVE_JOINT_1  | Target angle, 0.1° units, signed (e.g. 450 = 45.0°) |
| 0x11  | MOVE_JOINT_2  | As above |
| 0x12  | MOVE_JOINT_3  | As above |
| 0x13  | MOVE_JOINT_4  | As above |
| 0x14  | MOVE_JOINT_5  | As above |
| 0x15  | MOVE_JOINT_6  | As above |
| 0x20  | GRIPPER_OPEN  | Target position 0 (closed) to 1000 (fully open) |
| 0x21  | GRIPPER_CLOSE | Target position 0 (fully closed) to 1000 |

Action types 0x10–0x21 are provisional pending arm model selection. Actual joint count and parameter ranges depend on the robotic arm ordered.

### Reject reasons

| Value | Name                     | Condition |
|-------|--------------------------|-----------|
| 0x01  | KILL_SWITCH_ACTIVE       | Kill-switch GPIO is open |
| 0x02  | CONFIDENCE_BELOW_THRESHOLD | confidence < STM32H5 minimum (default 0.70) |
| 0x03  | SAFETY_BOUNDARY_VIOLATION | Requested position outside configured safe envelope |
| 0x04  | WATCHDOG_TIMEOUT         | Heartbeat not received within deadline; system in HALTED state |
| 0x05  | MALFORMED_FRAME          | CRC mismatch or length error |
| 0x06  | UNKNOWN_ACTION           | action_type not in the table above |
| 0x07  | PARAM_OUT_OF_RANGE       | action_param outside the range for this action_type |
| 0x08  | SYSTEM_FAULT             | STM32H5 in FAULT state |
| 0x09  | AUDIT_REF_ZERO           | audit_ref is 0 (log-before-act constraint violated) |
| 0x0A  | OVERSIGHT_OVERRIDE_ACTIVE | The oversight node has an override latched |
| 0x0B  | LATCH_OPEN               | The motor supply is physically cut at the relay |

### Halt triggers

| Value | Name                | Condition |
|-------|---------------------|-----------|
| 0x01  | KILL_SWITCH_GPIO    | NC button opened (physical kill switch) |
| 0x02  | WATCHDOG            | Heartbeat deadline expired |
| 0x03  | SAFETY_BOUNDARY     | Autonomous safety boundary breach detected by STM32H5 |
| 0x04  | LINUX_COMMANDED     | HALT action_type received from Linux |
| 0x05  | SUPERVISOR_OVERRIDE | Asserted by the UNO R4 WiFi oversight node |

### Override reasons

| Value | Name                      | Condition |
|-------|---------------------------|-----------|
| 0x01  | OPERATOR_BUTTON           | Physical NC button on the R4 opened |
| 0x02  | GOVERNANCE_HEARTBEAT_LOST | No SUPERVISOR_HEARTBEAT within the watchdog window |
| 0x03  | ATTESTATION_MISMATCH      | Audit digest stream gapped or rewound |
| 0x04  | REMOTE_CONSOLE            | Override raised on the R4's own Wi-Fi console |
| 0x05  | LATCH_MISMATCH            | The relay contact is not where it was told to be |

### Latch positions

| Value | Name    | Meaning |
|-------|---------|---------|
| 0x00  | OPEN    | Contact open, motor supply cut, HALT enforced physically |
| 0x01  | CLOSED  | Contact closed, motor supply available |
| 0x02  | UNKNOWN | Not read yet, or the sense channels are not complementary: cut harness, dead opto, flat battery |

The contact is normally open and wired in series with the motor supply, so OPEN is the safe state and the position an unlatched relay powers up in.

UNKNOWN must never be treated as evidence that the motors are isolated. Both sides implement this: `motor_power_cut` on the arbiter and `motors_isolated` on the governance tier are false before the first reading and false while the observation is UNKNOWN. Neither is allowed to claim safety it has not seen.

### Attestation verdicts

| Value | Name        | Condition |
|-------|-------------|-----------|
| 0x00  | CHAIN_OK    | audit_ref is exactly the previous one plus 1 |
| 0x01  | CHAIN_BREAK | audit_ref repeated or rewound: replay or rollback |
| 0x02  | GAP         | audit_ref skipped ahead: rows missing from the stream |

The R4 stores digests, not rows, so it cannot recompute the hash chain. These three verdicts are exactly what the digest stream alone can prove. Detecting an altered row is an offline reconciliation of the retained digests against the SQLite log, described in `r4-supervisor/README.md`.

---

## Timing requirements

### Actuation link

| Event | Deadline |
|-------|----------|
| STM32 ACK or REJECT after frame receipt | ≤ 1 ms |
| HALT_NOTIFY after halt-triggering event | ≤ 1 ms |
| Linux HEARTBEAT interval | 500 ms |
| STM32 watchdog deadline (no heartbeat received) | 1000 ms |
| STM32 HEARTBEAT_ACK after HEARTBEAT receipt | ≤ 5 ms |

### Oversight link

| Event | Deadline |
|-------|----------|
| SUPERVISOR_HEARTBEAT interval | 500 ms |
| R4 watchdog deadline (no supervisor heartbeat) | 2000 ms |
| ATTEST_ACK after ATTEST_DIGEST receipt | ≤ 5 ms |
| OVERRIDE_ASSERT after button press, debounced | ≤ 50 ms |
| Kill line asserted after override latches | next loop pass, under 10 ms |
| VENTUNO Q link timeout, silence counts as an override | 3000 ms |

The R4 watchdog is set at four heartbeat intervals so a single dropped frame does not halt the rig. The VENTUNO Q's own link timeout is longer still, because the two watchdogs are independent and should not both fire on the same transient.

---

## LED Matrix state machine

The Modulino LED Matrix reflects the STM32H5 system state. It is driven by the STM32H5 directly; the Linux side has no write path to it.

| State  | Display | Update trigger |
|--------|---------|----------------|
| ARMED  | Solid green | On transition to ARMED |
| BUSY   | Amber, slow pulse (1 Hz) | On transition to BUSY |
| HALTED | Solid red | On transition to HALTED; persists until power cycle or explicit resume command |
| FAULT  | Red, fast flash (4 Hz) | On transition to FAULT |

The HALTED state is sticky: once entered via kill-switch GPIO, the STM32H5 does not transition back to ARMED without an explicit resume sequence (TBD; at minimum requires the kill-switch GPIO to return to closed and a STATUS_QUERY from the Linux side confirming the Linux process is healthy).

---

## Governance rationale

### Log before act

The `audit_ref` field in COMMAND_REQUEST is a protocol-level enforcement of the "log before act" requirement (ISO 42001 §9.1). The Linux side must write the audit log entry and receive a confirmed row ID before the frame can be constructed. A zero `audit_ref` is rejected by the STM32H5 (reason: AUDIT_REF_ZERO). This creates a tamper-evident ordering guarantee: every executed command has a corresponding audit record that predates it.

### Dual confidence gate

Confidence is validated independently by both sides. The Linux side applies its own threshold before sending; the STM32H5 applies its own threshold (default 0.70, configurable at build time) on receipt. Neither side can unilaterally lower the effective threshold below what the other enforces.

### Watchdog as governance control

The heartbeat/watchdog mechanism implements COBIT DSS02 availability control at the hardware layer. If the Linux process hangs, crashes, or is compromised, the STM32H5 transitions to HALTED within one second without any software intervention from the Linux side.

### The latch relay, and why it is a control rather than an assertion

The physical enforcement path is a bistable relay contact in series with the motor supply, driven over the oversight node's own I2C bus. It replaced a GPIO line from the oversight node into the Alvik's kill-switch pin, and the two faults in that arrangement are worth recording because they are easy to repeat.

**It failed open.** Cut power to the board holding the line and the line released, so a power failure at the supervisor un-isolated the motors. A bistable contact holds its position with no coil current, so its state survives losing the board, losing Linux, and a reboot of either.

**It needed the governed component's cooperation.** The line worked only because the Alvik's firmware chose to read that pin. Reflash the Alvik and the control evaporated. That also broke the rule that governance modules do not attach to the governed component, since it made the robot a participant in its own restraint. A contact in the supply asks the Alvik for nothing.

The relay is owned by the oversight node and nothing else can reach it. The governance tier may request a position over `LATCH_REQUEST`, and the arbiter refuses a request to close while an override is latched. There is no message that overrides that refusal.

### Reading a control back, and why two sources

`LATCH_REPORT` carries three positions because a single one would rebuild the problem the report exists to solve.

The module's register is not an observation. Trusting it would put the system back where `stm32_ack` alone leaves it: the component that was asked to stop, reporting that it stopped. The sense pair on the contact is an observation, taken by the arbiter, of a physical thing. It is two channels rather than one so that a broken observation is reported as UNKNOWN rather than as a confident reading of the wrong position, which for a single pin is what a cut wire produces.

When they disagree, the disagreement is more informative than either agreeing with itself, and it latches an override on both sides independently. Neither side is allowed to rely on the other having noticed.

### Oversight as an independent function

The oversight link exists because the human override used to live inside the system it was meant to override: gesture HALT travelled through the AI perception pipeline, and the kill switch sat on the actuation MCU that the governance node commands. Both were real controls, and both depended on the health of what they supervised.

Three properties make the R4 independent rather than merely additional:

1. It is not on the command path. It produces no detection and issues no actuation command.
2. It is reached by its own link, and its state machine accepts no instruction over that link.
3. Its enforcement does not depend on the link at all. The relay contact in the motor supply holds whether or not the serial connection survives, and holds with no current at all once it has moved.

### Trust boundary of the oversight link

The oversight serial link is trusted to the extent that the physical USB-C cable is trusted. Anyone who can write to that cable can forge an OVERRIDE_CLEAR and release the governance filter's soft veto. This is tested rather than assumed, in `linux-stack/tests/test_security_oversight.py::TestSerialLinkTrustBoundary`.

That is why there are two enforcement paths. The forged clear reaches the soft veto and nothing else: the relay is driven from the R4's own latched state, no frame on any link can open or close it directly, and a LATCH_REQUEST asking for the contact to be closed is refused outright while an override stands. A control whose limits are undocumented is a control nobody can rely on, so the limit is written here rather than left to be discovered.

### Fail-closed on link loss

The VENTUNO Q treats silence from the oversight node as an override (`fail_closed`, the default). A supervisor that cannot be reached is not evidence that oversight is satisfied. The `--oversight-optional` flag disables this for bench work and the service warns when it is used.

### STM32H5 as sole execution authority

The protocol has no message type that grants the Linux side direct actuator access. Every actuation path passes through COMMAND_REQUEST / COMMAND_ACK. The STM32H5 may reject or ignore any request at any time. This is the segregation-of-duties control (COBIT APO01) expressed as a protocol constraint, not a policy statement.

---

## Open questions

- Sign the ATTEST_DIGEST payload with a key held only by the R4. The chain is currently unkeyed: it detects tampering with rows already witnessed, but a host that controls both the database and the link can forge a consistent chain going forward.
- Decide whether the governance filter should block on ATTEST_ACK before transmitting. It does not today: a GAP or CHAIN_BREAK verdict stops the next command, not the one in flight. Blocking would put the oversight link inside the actuation latency budget, which is the wrong trade for a link whose job is to observe.
- Define the read-back procedure for retained digests: currently a Wi-Fi console command, which is the least satisfying part of the design.
- Confirm the ABX00138 exposes a readable state register at all. The protocol treats it as a cross-check and does not depend on it, so a module without one costs nothing but a field in the report.
- Confirm UART baud rate and hardware flow control once official VENTUNO Q pinout is published
- Confirm whether the inter-processor bus supports shared memory; if so, evaluate latency vs UART
- Define the resume sequence for transitioning from HALTED back to ARMED
- Set the STM32H5 confidence threshold (default proposed: 0.70); consider whether this should be configurable at runtime or locked at build time
- Define the safe position envelope for SAFETY_BOUNDARY_VIOLATION (depends on arm model and mounting)
- Specify the joint count and parameter ranges once the robotic arm model is selected
