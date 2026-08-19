# R4 Supervisor: the oversight node

Firmware for the Arduino UNO R4 WiFi, Tier 0 of governed-edge-ai.

This board sits outside the perception to governance to actuation chain. It
runs no model, holds no camera and issues no command. Its only job is to watch
the governance tier and to be able to stop it.

## Why a fourth board

Before the R4 was added, the human override lived inside the system it was
meant to override. The gesture HALT travelled through the AI perception
pipeline; the kill switch sat on the actuation MCU that the governance node
itself commands. Both were real controls, and both depended on the health of
the thing they were supervising.

The R4 breaks that dependency. It is not on the command path, it is reached by
its own link, and nothing the governance tier can send to it clears an
override. An oversight function that its subject can switch off is not an
oversight function.

## What it does

| Function | Mechanism |
|---|---|
| Out-of-band human override | Physical NC button on D2. Latches, and stays latched after release. |
| Governance liveness watchdog | The VENTUNO Q must heartbeat every 500 ms. Silence beyond 2 s latches an override. |
| Audit attestation witness | Retains the last 64 audit hash-chain digests in its own memory, off the governance host. |
| Governance state annunciator | 12x8 LED matrix shows WATCHING, OVERRIDE, STALE or ATTEST. |
| Hard kill line | D3 drives the Alvik kill-switch input directly, independent of the serial link. |

## Two enforcement paths

This is the only board wired to both, and the difference matters.

1. **Soft veto**, over the serial link. `OVERRIDE_ASSERT` reaches the
   `GovernanceFilter`, which stops transmitting `CommandRequest` frames. The
   trust boundary here is the USB-C cable: anyone who can write to that cable
   can forge an `OVERRIDE_CLEAR`. This is stated plainly in
   `linux-stack/tests/test_security_oversight.py`, which tests the forgery.
2. **Hard line**, over GPIO. D3 drives the Alvik's kill-switch input. The
   Alvik firmware rejects every command while that pin reads active. No frame
   on any link can reach this path, so the forged clear above buys nothing.

The kill line is also held from boot until the first heartbeat arrives. A
governance tier that has not yet said anything has not yet earned the
authority to move a robot. There is no latch and no arming step: the line
releases on first contact.

## Wiring

| Pin | Direction | Connects to | Notes |
|---|---|---|---|
| D2 | input, `INPUT_PULLUP` | Override button, normally closed to GND | NC on purpose: a cut wire reads as pressed |
| D3 | output | Alvik kill-switch input (D4 on the Alvik) | Active low. Common ground required. |
| D4 | input, `INPUT_PULLUP` | Clear button, normally open to GND | Momentary. The state machine may refuse the clear. |
| USB-C | serial | VENTUNO Q | 921600 baud |

The two boards must share a ground for the kill line to mean anything. A
floating line reads as noise, which is the one failure mode this design cannot
detect on its own.

## Files

| File | Contents |
|---|---|
| `r4_supervisor.ino` | Sketch: pins, LED matrix glyphs, serial I/O, optional Wi-Fi console |
| `supervisor_state.h` / `.cpp` | The state machine. No Arduino headers: pure logic, host-compilable. |
| `ipc_frame.h` / `.cpp` | IPC codec, oversight subset. CRC-16/CCITT, five message types. |
| `test/parity_harness.cpp` | Host driver that exposes the two logic files over a line protocol |

The sketch deliberately implements no decoder for `COMMAND_REQUEST`. This board
is not on the actuation path, and leaving those decoders out reduces what it
can be talked into doing.

## How this firmware is tested

There is no Arduino toolchain in CI and no board attached, so the sketch itself
is not executed by the test suite. Everything that decides behaviour, though,
lives in `supervisor_state.cpp` and `ipc_frame.cpp`, which are plain C++ with
no Arduino headers.

`linux-stack/oversight/mock_supervisor.py` is the executable specification: a
Python model of the same state machine, driven over a pty exactly as the real
board is driven over USB-C. The C++ here is a port of it.

`linux-stack/tests/test_r4_firmware_parity.py` compiles these two files for the
host with `-Wall -Wextra -Werror`, drives them through
`test/parity_harness.cpp`, and checks them against the Python codec and the
reference model: byte-identical frames, identical verdict sequences, identical
state transitions, identical constants. Two implementations of one state
machine drift unless something checks them.

Run it with the rest of the suite:

```bash
make test              # includes the parity tests
cd linux-stack && python3 -m pytest tests/test_r4_firmware_parity.py -v
```

The parity tests skip, with a reason, when no C++ compiler is present. What is
not covered here is the Arduino layer itself: pin behaviour, the LED matrix
driver, Wi-Fi, and `Serial` timing at 921600 baud. Those need the board.

To compile the harness by hand:

```bash
cd r4-supervisor/test
g++ -std=c++17 -Wall -Wextra -Werror -I.. -o parity_harness \
    parity_harness.cpp ../ipc_frame.cpp ../supervisor_state.cpp
echo -e "TICK 100\nSTATE\nQUIT" | ./parity_harness
```

## Building and uploading

Board: **Arduino UNO R4 WiFi** (`arduino:renesas_uno:unor4wifi`).

With `arduino-cli`:

```bash
arduino-cli core install arduino:renesas_uno
arduino-cli lib install "Arduino_LED_Matrix"
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi r4-supervisor
arduino-cli upload  --fqbn arduino:renesas_uno:unor4wifi -p /dev/ttyACM0 r4-supervisor
```

With the Arduino IDE: open `r4_supervisor.ino`, select **Arduino UNO R4 WiFi**
under Tools > Board, then Upload. The IDE compiles every `.cpp` in the sketch
folder, so `ipc_frame.cpp` and `supervisor_state.cpp` are picked up
automatically. `test/` is ignored by the IDE, which is why the harness lives
in a subfolder.

## Optional Wi-Fi console

Off by default. Set `ENABLE_WIFI_CONSOLE` to 1 in the sketch and add an
`arduino_secrets.h`:

```c
#define SECRET_SSID "your-network"
#define SECRET_PASS "your-password"
```

The console listens on TCP 8021 and accepts three line commands: `STATUS`,
`OVERRIDE`, `CLEAR`. It is unauthenticated and LAN-only, on the same
assumption as the audit dashboard.

Leaving it off is the better default. An oversight node with fewer network
surfaces is a better oversight node, and the physical button needs no network
at all. `arduino_secrets.h` is gitignored; do not commit credentials.

## Attestation, and what it does not prove

The R4 stores digests, not audit rows, so it cannot recompute the hash chain.
Its live verdict covers what the digest stream alone can prove:

| Verdict | Condition |
|---|---|
| `CHAIN_OK` | `audit_ref` is exactly the previous one plus 1 |
| `GAP` | `audit_ref` skipped ahead: rows are missing from the stream |
| `CHAIN_BREAK` | `audit_ref` repeated or rewound: replay or rollback |

Detecting an *altered* row is an offline job. Read the retained digests back
from the node and hand them to
`oversight.attestation.verify_database(conn, retained=...)`, which recomputes
the chain from SQLite and reconciles it against what the node witnessed. The
first altered row changes every digest after it.

The chain is unkeyed. Someone who controls the governance host and this link
can forge a consistent chain over falsified rows going forward. What it detects
is tampering with rows already witnessed, which is the property the audit
argument needs. Signing the digests with a key held only by the R4 is the next
increment, and it is on the open-decisions list in
`docs/architecture.md`.

## Clearing an override

The override latches. It does not lapse when its cause goes away, and the
governance tier has no message that clears it. Releasing one is a physical act
at this board: press the clear button on D4.

The state machine refuses the clear while the override button is still held, or
until a heartbeat has actually arrived and is still fresh. Clearing an override
whose cause is still present would put the rig straight back into the state
that raised it.

Clearing an attestation override also resynchronises the expected reference to
whatever the governance tier last reported, otherwise every later digest would
gap against a stale expectation and the node could never resume. The gap itself
does not disappear: it stays in the retained digests, and the clear is the
record that an operator looked at it and accepted it.

## Licence

Apache 2.0, as the rest of the code in this repository.
