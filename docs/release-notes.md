# Release Notes

Bodies for the GitHub Releases, kept here so they are versioned alongside what
they describe. Each one describes the tree at its own tag, not the tree today.
Where a later release supersedes something, the entry says so rather than being
edited to pretend otherwise.

---

## v3.0.0: the latch relay

**Tag:** `v3.0.0` · Tagger: Thierry Sayegh-Sauvage

Physical enforcement moves off the governed board. Where v2.0.0 ran a GPIO line
from the oversight node into the Alvik's kill-switch pin, v3.0.0 puts a bistable
relay contact in the Alvik's motor supply, held on the oversight node's own I2C
bus. The robot no longer participates in its own restraint, and the stop no
longer depends on any board staying powered.

### Why the line had to go

Two faults, and the second is worse than the first.

**It failed open.** Cut power to the oversight node and the line released, so a
power failure at the supervisor un-isolated the motors. A safety control that
stops enforcing when its own board dies is not a safety control.

What made this uncomfortable is that v2.0.0 shipped with 611 passing tests and
none of them could have caught it. The mocks modelled a state machine, so there
was no power to lose. **Coverage does not find a fault whose failure mode the
model has no vocabulary for.** `SimulatedLatch` now models a contact rather
than a boolean, and `TestBistability` is the test that could not previously
have existed.

**It needed the governed component's cooperation.** The line worked only
because `alvik-firmware/main.py` chose to read that pin. Firmware on the board
under review is a software gate wearing a hardware costume: reflash the Alvik
and the control evaporates. That broke the project's own design rule, which
says a governance module does not attach to the component it governs, while the
documentation described the line as the path that could not be reached from
software.

A bistable contact in the motor supply has neither fault. It holds position
with no coil current, so it survives a power cut at every board in the rig and
a reboot of the decision host, and it is in the supply, so the Alvik has
nothing left to agree to. The `KILL_PIN` on the Alvik remains as a local test
input for firmware work and is documented as **not** a governance control.

### Four invariants, one of which now holds with everything switched off

| | Invariant | Enforced by |
|---|---|---|
| A | Log before act | The MCU rejects any `CommandRequest` with `audit_ref == 0` |
| B | Enforcement outlives its enforcer | The latch contact, which holds with no current at all |
| C | Witness before act | Chain digests retained on a board the host does not control |
| D | Oversight is not revocable by its subject | No inbound message releases the latch |

B is new in this release, and it is the only one of the four that holds with
every processor in the system switched off.

### Who owns the relay

The arbiter, exclusively, and the asymmetry is the point. The governance tier
may send `LATCH_REQUEST(OPEN)`, which is **always honoured**, because more ways
to stop are safe. It may send `LATCH_REQUEST(CLOSED)`, which is **refused
outright** while an override stands. Nothing on any link can talk the override
down, which is why the relay sits on the arbiter's bus and not the decision
host's.

The contact is opened before anything else runs at boot. Bistable means it
comes up wherever it was left rather than in a safe default, so the arbiter
starts from `UNKNOWN` and finds out instead of assuming. The first heartbeat is
what closes it: a governance tier that has not yet said anything has not yet
earned the authority to move a robot.

### Reading the contact back, and why it takes two channels

A command whose effect is never checked is an assertion. The arbiter reads three
things every 100 ms: what it last commanded, what the module's own MCU reports
over I2C, and what a sense circuit observes.

The register is a cross-check, never the observation. A small MCU behind an I2C
interface most likely echoes the last command it accepted rather than observing
where the contact sits, and believing it would reproduce the exact error the
read-back exists to remove: the component that was told to stop reporting that
it stopped.

**The sense circuit is two channels, and that came out of writing the
deployment guide rather than out of the tests.** The first implementation read
one pin, high meaning open. Part 6 of the guide forced the question of what
that pin reads when its wire is cut, and the answer was "open", which the
arbiter would have reported as *the motors are isolated*. On no evidence at
all. Wiring it the other way round only moves the problem: whichever way a
single input is arranged, one of its two readings is also what a broken wire
produces, so one contact position becomes indistinguishable from a fault.

The observation is now antivalent, which is old safety-engineering practice:
two opto-isolated channels that must disagree with each other.

| A, "contact open" | B, "motor rail live" | Decoded |
|---|---|---|
| Lit | Dark | `OPEN` |
| Dark | Lit | `CLOSED` |
| Dark | Dark | `UNKNOWN`: cut harness, dead opto, flat battery |
| Lit | Lit | `UNKNOWN`: shorted harness |

Every fault in the observation lands in `UNKNOWN`, and nothing rounds `UNKNOWN`
up to isolation. The cost is availability: a broken sense wire stops the rig.
That is the correct direction for this trade. Opto isolation also removes the
shared ground the old GPIO line depended on, which was the one failure that
design admitted it could not detect for itself.

One residual property is documented rather than hidden: only the energised
channel is under test at any instant, so a break in the dark one is latent until
the contact next moves. It surfaces on the next transition, because every
command reads back.

### Five boards, one job each

`docs/architecture-reconciliation.md` reads the published governance-chain
diagram against the codebase: a fifteen-row delta register, eight decisions
taken, four reasoned defaults.

| Board | Single job | Decides? | Enforces? |
|---|---|---|---|
| UNO Q 4GB | Witness: an independent second observation | no | no |
| VENTUNO Q | Decision: perception, governance filter, audit journal | yes | no |
| Alvik | Governed body: executes, and may refuse | no | self only |
| UNO R4 WiFi | Safety arbiter: relay, buttons, annunciator, digest witness | no | **yes** |
| Nesso N1 | Out-of-band human supervision | no | via signed lift |

**No board both decides and enforces.** That sentence is the architecture, and
it is checkable by looking at the wiring rather than by reading a policy.

The Modulino Hub, Buttons, Pixels and Buzzer are dropped as redundant with the
arbiter, which already has buttons, a matrix and a Qwiic port. Distance and
Movement stay: they are the two doing real work, a safety envelope outside the
vision pipeline and proof of stop. Both attach to the arbiter's bus, not the
decision host's, because a governance module should not hang off the board that
decides any more than off the board that is governed.

**The Nesso N1 is in the architecture and has no firmware.** It is build step
13. Nothing in this release depends on it, and the documentation says so
wherever it appears.

### Cameras, and three specifications with governance consequences

Settled: Arducam IMX219 8 MP, two of them, splayed for roughly 120°. That
retires the longest-running open item in the project. Three of its
specifications went into the threat model rather than the parts list:

- **200 mm minimum focus** leaves the near field blurred exactly where the risk
  is highest, which is what moves the ToF module from nice-to-have to covering
  a known hole in the primary sensor.
- **Rolling shutter** means a frame is not a moment, which is a real error term
  in any claim about where something was when the system decided to stop.
- **62.2° per camera** means the audit log will faithfully record that nothing
  was detected in a blind sector.

### Protocol

`docs/ipc-protocol.md` v0.3. Two new message types on the oversight link,
fifteen in total:

| Type | Code | Direction | Payload |
|---|---|---|---|
| `LATCH_REQUEST` | `0x32` | Governance to arbiter | 9 bytes |
| `LATCH_REPORT` | `0xA3` | Arbiter to governance | 11 bytes |

Plus `RejectReason.LATCH_OPEN = 0x0B`, `OverrideReason.LATCH_MISMATCH = 0x05`,
and a fifth annunciator glyph, `LATCH`, for a relay that is not where it was
told to be or cannot be seen.

Additive on the wire. The thirteen existing message types are unchanged.

### Breaking changes

**Wiring.** The jumper from the oversight node's D3 into the Alvik's D4 must be
removed. D3 is now an input, D5 is new, and both are pulled up. The relay goes
on the Qwiic bus at `0x2A` with two opto-isolated sense channels.
`docs/deployment-guide.md` Part 6 has the build in full, including the two
verification tests that prove the sense circuit is not decorative.

**API.** `MockR4Supervisor.kill_line_asserted` is removed and replaced by two
properties that were previously conflated:

| Property | Means |
|---|---|
| `motor_power_cut` | The contact has been **observed** open. Never true on an `UNKNOWN` reading. |
| `halt_intended` | The arbiter has **decided** to stop the rig. |

They differ exactly when something is wrong, and collapsing them was how a
system could claim an isolation it had not seen. `SupervisorLink` gains
`last_latch`, `motors_isolated` and `request_latch()`.

**Simulator.** `SimulatedLatch.inject_sense_failure()` now takes a channel,
`"a"`, `"b"` or `"both"`, defaulting to `"both"`.

No schema change. A v2.0.0 audit database is read without modification.

### QA

703 tests across two modules, 100% line coverage on both, gate at 98. ruff,
mypy strict, bandit and pip-audit clean.

The parity harness now compiles `latch.cpp` alongside the state machine with
`-Wall -Wextra -Werror`. The sense glue is `digitalRead`, which the harness
cannot drive, so it is checked as text instead: both pins present, both pulled
up, and `LATCH_UNKNOWN` still reachable from the glue.

### Not tested

The Arduino hardware layer, and this release adds to that list rather than
shortening it. Pin timing, the LED matrix driver, Wi-Fi, serial throughput at
921600 baud, contact bounce, coil pulse adequacy, inrush on the motor supply,
opto forward current at the Alvik's cell voltage, and the switching thresholds
of both sense channels. Every timing figure in the protocol specification is a
design target, not a measurement.

Three assumptions are cheap to settle on a bench and each changes only its own
paragraph if wrong: that the relay module's register echoes its command, that
its I2C address is `0x2A`, and that a 50 ms coil pulse is right.

### What comes next

| Step | Work |
|---|---|
| 12 | Arbiter as governance bus owner: I2C layer, third button, ALLOW / GATED / HALT glyphs |
| 13 | Nesso N1: verdict stream, display, signed HALT lift, key pairing |
| 14 | Audit journal signing, countersigned by the Nesso |
| 15 | Distance and Movement: evidence outside the vision pipeline, proof of stop |
| 16 | Witness UNO Q and the agreement gate |
| 17 | STM32H5 Zephyr firmware, motor-side timing only. Blocked on an unpublished pinout. |

The arbiter role stays on the UNO R4 WiFi permanently, which is what takes step
17 off the critical path for everything above it.

---

## v2.0.0: the oversight tier

**Tag:** `v2.0.0` at `d2838a4` · Tagger: Thierry Sayegh-Sauvage

> **Read this first.** This release adds an independent oversight board and a
> physical enforcement path. That path was a GPIO line from the oversight board
> into the Alvik's kill-switch pin, and it has since been replaced. The reasons
> are worth reading and they are at the end of these notes, under *What this
> release got wrong*. Everything else here still stands.

### What it adds, and the weakness it corrects

The three-board version had the human override living inside the system it was
meant to override. The gesture HALT travelled through the AI perception
pipeline. The kill switch sat on the actuation MCU that the governance node
itself commands. Both were real controls, and both went down with the thing
they supervised.

In Three Lines Model terms the architecture had a strong second line and no
third. That is the specific confusion that makes an oversight function
unreliable in practice: it works exactly until the thing it supervises is the
thing that failed. The test that separates a second-line control from
third-line assurance is not where it sits on a diagram, it is whether the
supervised function can switch it off.

An **Arduino UNO R4 WiFi** becomes Tier 0. It is not on the command path, it is
reached by its own link, and nothing the governance tier can send to it clears
an override. `test_no_message_type_clears_an_override` asks that question in
code by throwing the entire outbound vocabulary at a latched node.

**Why the least capable board in the rig.** The whole oversight firmware is a
few hundred lines of C++ with no scheduler, no filesystem and no network stack
in the default build. Small enough to read in one sitting, which is what a
supervisor should be. The argument is that the supervisor should be simpler
than the supervised, so its correctness is checkable by inspection.

### Two enforcement paths

| Path | Mechanism | Survives |
|---|---|---|
| Soft veto | `OVERRIDE_ASSERT` over serial; the governance filter stops transmitting `CommandRequest` frames | A hung or misbehaving perception tier |
| Physical | A line the governance tier cannot reach from any message | A compromised governance host, a cut cable, a forged `OVERRIDE_CLEAR` |

Building only the soft path would have produced a control with a single point
of failure that a document would then have described as independent.

The physical path is held from boot until the first heartbeat arrives. Not a
latch and no arming step, just `override || !heartbeat_seen`. A governance tier
that has not yet said anything has not yet earned the authority to move a
robot.

### Witness before act

Each stored audit row is read back from SQLite and folded into a chain:
`digest_n = SHA-256(digest_{n-1} || canonical(row_n))`. The head is published
to the oversight node **before** the command frame is written, and the node
retains the last 64 digests in its own memory.

That ordering is the whole point. It turns the retained digests into evidence
rather than a log of a log. Reconciling a recomputed chain against them detects
edits, deletions, backdating and reordering of rows already witnessed.

The chain hashes the **stored** row, not the caller's intent. `fetch_event()`
reads it back before hashing, at a cost of one indexed `SELECT` per event.
Hashing the caller's object would have been free and would have proved nothing
about what the database holds.

### What the security tests assert, including what does not hold

`test_security_oversight.py` attacks the design from four positions: a
compromised governance host, an attacker on the oversight cable, a compromised
host with database write access, and hostile input on either link.

It asserts what holds **and what does not**. A forged `OVERRIDE_CLEAR` really
does release the soft veto, and the test says so. The physical path is
unaffected, which is why there are two. The chain is unkeyed, so a host
controlling both the database and the link can forge a consistent chain over
rows written after a compromise; it protects rows already witnessed, which is
the property the audit argument needs. A control whose limits are undocumented
is a control nobody can rely on.

**Two real defects were found this way rather than by review.** A missing
frame-length guard let one hostile header wedge a link permanently. And a
failed transmit left the audit log claiming a command had been sent. Both fixed,
both with regression tests.

### Firmware parity

`MockR4Supervisor` is the executable specification and the C++ is the port, not
the other way round. The Python model carries the test suite.
`test_r4_firmware_parity.py` compiles the firmware logic for the host with
`-Wall -Wextra -Werror` and checks byte-identical frames, identical verdict
sequences, identical state transitions and identical constants. Two
implementations of one state machine drift unless something checks them.

### Also in this release

- Governance contract invariants 7 (oversight veto) and 8 (witness before act)
- Five oversight message types, `docs/ipc-protocol.md` v0.2
- A third audit actor, `oversight`: machine-initiated supervisor action reads
  differently from a person pressing a button, and an auditor after an incident
  needs to tell them apart
- Fail-closed on oversight loss. Silence from the node counts as a veto. A
  supervisor that cannot be reached is not a satisfied supervisor.
- `docs/deployment-guide.md`: bare metal to a verified rig, for a reader with
  no prior embedded experience

### Breaking change

The audit schema gained an `oversight` actor and a new `detection_type`. SQLite
cannot alter a `CHECK` constraint in place, so **a pre-existing database must be
rebuilt**. See `audit-service/schema.sql` and `docs/architecture.md` section 6.

Nothing else breaks. The eight original IPC message types are unchanged on the
wire, and a deployment with no oversight board runs with `--supervisor none`,
or `--oversight-optional` for bench work, which the service warns about when
used.

### QA

611 tests across two modules, 100% line coverage on both, coverage gate raised
from 90 to 98. ruff, mypy strict, bandit and pip-audit clean. Two pre-existing
bandit findings were resolved rather than suppressed.

Everything runs without physical hardware. The mocks are real implementations
of their state machines driven over pseudo-terminals, so the path exercised in
CI is the one that runs on the rig.

### Not tested

The Arduino hardware layer. Pin timing, the LED matrix driver, Wi-Fi, serial
throughput at 921600 baud and the electrical behaviour of the physical path all
need the board. Every timing figure in the protocol specification is a design
target, not a measurement. `docs/architecture.md` section 12 lists them.

### What this release got wrong

Two faults in the physical path, both found after tagging and both fixed in the
work that follows this release. They are recorded here because a release note
that only lists what worked is marketing.

**The line failed open.** Cut power to the oversight board and it released, so a
power failure at the supervisor un-isolated the motors. A safety control that
stops enforcing when its own board dies is not a safety control. None of the
611 tests above could have caught it: the mocks modelled a state machine, so
there was no power to lose. Coverage does not find a fault whose failure mode
the model has no vocabulary for.

**The line needed the governed component's cooperation.** It worked only
because the Alvik's firmware chose to read that pin. Firmware on the board
under review is a software gate wearing a hardware costume, and reflashing the
Alvik would have removed the control entirely. This broke the project's own
design rule while these notes described it as the path that could not be
reached from software.

The replacement is a bistable relay contact in the Alvik's motor supply, held
on the oversight board's own I2C bus. It holds position with no current at all,
and the governed board has nothing left to agree to. See **v3.0.0**,
`docs/architecture.md` v3.0, and `docs/architecture-reconciliation.md` for the
reasoning in full.

---

## v1.0.0: three-board governance demonstrator

**Tag:** `v1.0.0` at `838d090` · Tagger: Thierry Sayegh-Sauvage · Tagged
retroactively at the release of v2.0.0.

The complete eight-step build: AI governance invariants enforced at the
hardware and protocol level across three Arduino boards.

| Board | Role |
|---|---|
| UNO Q 4GB | Perception, multi-backend inference, TCP transport |
| VENTUNO Q | Governance filter, append-only SQLite audit log, IPC dispatch |
| Alvik | Actuation, four independent firmware gates |

**Log-before-act as a structural constraint.** `audit_ref` is a SQLite rowid
obtained before any `CommandRequest` frame is transmitted, and the MCU rejects
`audit_ref == 0` at the protocol layer, unconditionally, before any other
check. There is no code path that sends without logging: the send sits inside
the block that follows the log call.

Dual-layer confidence gate (float64 on Linux, float32 on the MCU, enforced
independently), one command per frame with every other detection logged as
suppressed, full suppression record, and ACK/REJECT tracking where a timeout
leaves `stm32_ack` NULL rather than false, because "no response" and "response:
rejected" are different facts after an incident.

313 tests across two modules, 95%+ coverage each, hardware-free CI via a
pty-based co-processor mock and loopback TCP.

**Superseded by v2.0.0**, which adds an independent oversight tier. The human
override in this release lives inside the system it is meant to override, which
is the weakness v2.0.0 exists to correct.
