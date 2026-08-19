# Release Notes

Bodies for the GitHub Releases, kept here so they are versioned alongside what
they describe. Each one describes the tree at its own tag, not the tree today.
Where a later release supersedes something, the entry says so rather than being
edited to pretend otherwise.

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
and the governed board has nothing left to agree to. See the current `main`,
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
