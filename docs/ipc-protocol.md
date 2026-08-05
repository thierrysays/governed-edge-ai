# IPC Protocol Specification: Linux (NPU) to STM32H5

Version 0.1 — draft, pending pinout confirmation  
Status: proposed, not yet implemented

## Purpose

This document specifies the inter-processor communication (IPC) protocol between the Linux/NPU side and the STM32H5 real-time co-processor on the Arduino VENTUNO Q. It is a governance artefact as much as a technical one: the protocol is the mechanism by which authority separation is enforced at the message layer.

The Linux side may send command requests. The STM32H5 may accept or reject them. No path exists by which the Linux side can compel execution.

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

### Linux to STM32H5

| Type byte | Name            | Direction      |
|-----------|-----------------|----------------|
| 0x01      | COMMAND_REQUEST | Linux → STM32H5 |
| 0x10      | HEARTBEAT       | Linux → STM32H5 |
| 0x20      | STATUS_QUERY    | Linux → STM32H5 |

### STM32H5 to Linux

| Type byte | Name             | Direction       |
|-----------|------------------|-----------------|
| 0x81      | COMMAND_ACK      | STM32H5 → Linux |
| 0x82      | COMMAND_REJECT   | STM32H5 → Linux |
| 0x90      | HALT_NOTIFY      | STM32H5 → Linux |
| 0x11      | HEARTBEAT_ACK    | STM32H5 → Linux |
| 0x21      | STATUS_RESPONSE  | STM32H5 → Linux |

---

## Payload definitions

### COMMAND_REQUEST (0x01) — 20 bytes

The Linux side must write the audit log entry and obtain a confirmed entry ID before constructing this frame. The `audit_ref` field carries that ID. A command request sent without a valid `audit_ref` is rejected.

| Offset | Length | Field         | Notes |
|--------|--------|---------------|-------|
| 0      | 8      | audit_ref     | uint64: audit log entry ID written before this frame |
| 8      | 4      | timestamp_us  | uint32: microseconds since session start |
| 12     | 1      | actor         | 0x01 = ai, 0x02 = human_override |
| 13     | 4      | confidence    | float32 (IEEE 754): 0.0 to 1.0 |
| 17     | 1      | action_type   | see action type table |
| 18     | 2      | action_param  | int16: action-specific parameter |

### COMMAND_ACK (0x81) — 9 bytes

| Offset | Length | Field     | Notes |
|--------|--------|-----------|-------|
| 0      | 8      | audit_ref | uint64: echoed from the request |
| 8      | 1      | status    | 0x00 = queued, 0x01 = executing |

### COMMAND_REJECT (0x82) — 9 bytes

| Offset | Length | Field     | Notes |
|--------|--------|-----------|-------|
| 0      | 8      | audit_ref | uint64: echoed from the request; 0 if frame was malformed |
| 8      | 1      | reason    | see reject reason table |

### HALT_NOTIFY (0x90) — 9 bytes

Sent by the STM32H5 within 1 ms of any halt-triggering event, without waiting for a request.

| Offset | Length | Field        | Notes |
|--------|--------|--------------|-------|
| 0      | 8      | timestamp_us | uint64: microseconds since session start |
| 8      | 1      | trigger      | see halt trigger table |

### HEARTBEAT (0x10) — 0 bytes payload

No payload. The Linux side sends this every 500 ms.

### HEARTBEAT_ACK (0x11) — 0 bytes payload

No payload. Sent by the STM32H5 in response to each HEARTBEAT.

### STATUS_QUERY (0x20) — 0 bytes payload

No payload.

### STATUS_RESPONSE (0x21) — 14 bytes

| Offset | Length | Field              | Notes |
|--------|--------|--------------------|-------|
| 0      | 1      | system_state       | see system state table |
| 1      | 1      | kill_switch_gpio   | 0x00 = NC closed (normal), 0x01 = open (halt triggered) |
| 2      | 4      | commands_received  | uint32: since session start |
| 6      | 4      | commands_rejected  | uint32: since session start |
| 10     | 4      | commands_executed  | uint32: since session start |

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

### Halt triggers

| Value | Name              | Condition |
|-------|-------------------|-----------|
| 0x01  | KILL_SWITCH_GPIO  | NC button opened (physical kill switch) |
| 0x02  | WATCHDOG          | Heartbeat deadline expired |
| 0x03  | SAFETY_BOUNDARY   | Autonomous safety boundary breach detected by STM32H5 |
| 0x04  | LINUX_COMMANDED   | HALT action_type received from Linux |

---

## Timing requirements

| Event | Deadline |
|-------|----------|
| STM32H5 ACK or REJECT after frame receipt | ≤ 1 ms |
| HALT_NOTIFY after halt-triggering event | ≤ 1 ms |
| Linux HEARTBEAT interval | 500 ms |
| STM32H5 watchdog deadline (no heartbeat received) | 1000 ms |
| STM32H5 HEARTBEAT_ACK after HEARTBEAT receipt | ≤ 5 ms |

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

### STM32H5 as sole execution authority

The protocol has no message type that grants the Linux side direct actuator access. Every actuation path passes through COMMAND_REQUEST / COMMAND_ACK. The STM32H5 may reject or ignore any request at any time. This is the segregation-of-duties control (COBIT APO01) expressed as a protocol constraint, not a policy statement.

---

## Open questions

- Confirm UART baud rate and hardware flow control once official VENTUNO Q pinout is published
- Confirm whether the inter-processor bus supports shared memory; if so, evaluate latency vs UART
- Define the resume sequence for transitioning from HALTED back to ARMED
- Set the STM32H5 confidence threshold (default proposed: 0.70); consider whether this should be configurable at runtime or locked at build time
- Define the safe position envelope for SAFETY_BOUNDARY_VIOLATION (depends on arm model and mounting)
- Specify the joint count and parameter ranges once the robotic arm model is selected
