# State of Play

**governed-edge-ai, as at 19 August 2026, tag `v3.0.0`.**

A source of truth for anyone writing about this project: the website, a talk, a deck, a post. It states what is true today, what is designed but not built, what has not been tested, and which older claims are now false. It prescribes no wording and no markup.

Every figure here was read off the repository, not recalled.

---

## The project in one paragraph

An open-source demonstrator showing that AI governance controls can be enforced in circuitry rather than described in policy. Five Arduino boards, one job each. One watches the world, one decides and writes a tamper-evident journal, one is the governed robot, one arbitrates safety from outside the command chain, one is an out-of-band operator console. No motor command is transmitted before its audit entry exists, no audit entry goes unwitnessed by a board the deciding host does not control, and no software anywhere can restore motor power once the arbiter has opened a relay contact.

---

## The three claims the project actually makes

Written as claims because each one is checkable, and each has a test that names it.

**Log before act.** The audit reference is a database row id obtained before any command frame is transmitted. The structure prevents the alternative: the send sits inside the block that follows the log call, and the robot's own firmware rejects any command that does not carry a reference.

**Witness before act.** A SHA-256 chain head reaches a board the deciding host does not control before the command frame is written. That ordering is what makes the retained digests evidence rather than a log of a log.

**Enforcement outlives its enforcer.** The stop is a bistable relay contact in the robot's motor supply. It holds position with no current at all, so cutting power to the board that opened it does not restore motor power, and it needs no cooperation from the robot because the robot has no pin to read.

The third is new at v3.0.0 and it is the one with a story attached. See *How the current design was arrived at*.

---

## Hardware

| Board | Single job | Decides? | Enforces? | Status |
|---|---|---|---|---|
| Arduino UNO Q 4GB | Witness: an independent second observation, whose disagreement forces a HALT | no | no | Running perception today; the witness role is build step 16 |
| Arduino VENTUNO Q | Decision path, explicitly revocable: perception, governance filter, audit journal | yes | no | Running |
| Arduino Alvik | Governed body: executes, and refuses any command without a valid audit reference | no | itself only | Running |
| Arduino UNO R4 WiFi | Safety arbiter: relay, override button, annunciator, off-host digest witness | no | **yes** | Running |
| Arduino Nesso N1 | Out-of-band operator console | no | via signed lift | **Designed, no firmware.** Build step 13. |

**The organising rule: no board both decides and enforces.** That is checkable by looking at the wiring rather than by reading a policy.

On the arbiter's own I2C bus, deliberately not the deciding host's: a Modulino Latch Relay whose contact sits in the motor supply, plus Distance and Movement modules for evidence outside the vision pipeline (build step 15, not yet built).

**Cameras:** Arducam IMX219 8 MP, two of them, splayed for roughly 120 degrees of coverage.

---

## Numbers

| | |
|---|---|
| Current release | `v3.0.0`, "the latch relay" |
| Build steps shipped | 11 |
| Build steps designed but not built | 6 (steps 12 to 17) |
| Tests | 703 across two modules |
| Line coverage | 100% on both, gate set at 98% |
| Static analysis | ruff, mypy strict, bandit, pip-audit, all clean |
| Hardware needed to run the suite | None |
| Code licence | Apache 2.0 |
| Hardware design files | CERN OHL-P v2 |
| Documentation | CC BY 4.0 |

The full suite runs with no physical hardware. The test doubles are real implementations of the state machines driven over pseudo-terminals, so the path exercised in CI is the one that runs on the rig.

---

## How the current design was arrived at

Worth telling, because the project's credibility rests on it rather than on the feature list.

The physical stop used to be a signal wire from the arbiter into a pin on the robot. It had two faults.

It **released when the arbiter lost power**. A safety control that stops enforcing the moment its own board dies is not a safety control.

It **needed the governed component's cooperation**. It worked only because the robot's firmware chose to read that pin. Reflash the robot and the control evaporates. That is a governance module bolted onto the thing it is meant to govern, which breaks the project's own design rule.

Neither fault appeared in 611 passing tests. The test doubles modelled a state machine, so there was no power to lose. **Coverage does not find a fault whose failure mode the model has no vocabulary for.**

A second fault surfaced while writing the deployment instructions rather than from the tests. The arbiter read the contact back on a single sense line, and the question "what does that line read when its wire is cut" had the answer "open", which the arbiter reports as *the motors are isolated*. The observation is now two opto-isolated channels wired to disagree with each other: any fault reads as "cannot see", and nothing rounds that up to "isolated".

---

## What is not true any more

Older material states these. All are now false.

| Stale claim | Correct today |
|---|---|
| Three boards | Five |
| Four boards | Five |
| 241 tests, 95.76% coverage | 703 tests, 100% line coverage |
| 611 tests | 703. The 611 figure is still usable when the point is that those tests missed the power-loss fault. |
| Eight build steps | Eleven shipped |
| A hardware kill switch on the robot | A bistable relay contact in the motor supply, held by a board that is not on the command chain |
| MIT licence | Apache 2.0 |
| Camera unsourced | Arducam IMX219, two of them |
| Reproducible for under EUR 200 | **No figure.** That was true of the three-board rig. Five boards plus a relay, optos and two cameras is well past it. The honest claim is commodity parts and no bespoke silicon. |

The cost figure is the one to be most careful with. Repeating it would put a false statement on a page whose argument is about not overclaiming.

---

## What the project does not solve

These belong in any serious write-up. Omitting them would contradict the argument the project makes.

**The audit chain is unkeyed.** It detects tampering with rows already witnessed. A host controlling both the database and the link can forge a consistent chain over rows written after a compromise. Signing is build step 14.

**The oversight link is worth exactly what the cable is worth.** Anyone who can write to it can forge a message releasing the soft veto, and a test demonstrates precisely that. The relay contact is reachable by no message at all, which is why there are two paths rather than one.

**Physical access to the motor wiring bypasses everything.** Physical enforcement assumes physical custody of the rig.

**The 0.70 confidence threshold is an engineering judgment.** No published standard maps a confidence score to an injury probability for human-robot collaboration.

**Nothing has been powered on.** No board has run this firmware, no relay has been wired, no camera has been mounted. Pin timing, the LED matrix, serial throughput, contact bounce, coil pulse adequacy and both sense-channel thresholds are all untested. Every timing figure in the protocol specification is a design target, not a measurement.

That last point is the one most likely to be softened by accident. The right register is that the software is complete and tested and the hardware is a design awaiting a bench.

---

## Where the detail lives

| Document | Contents |
|---|---|
| `README.md` | The project and its argument, in short |
| `docs/architecture.md` | Full specification, v3.0. Section 12 lists every untested hardware claim. |
| `docs/architecture-reconciliation.md` | Why five boards, the delta register, the decisions taken |
| `docs/deployment-guide.md` | Bare metal to a verified rig, v2.0, for a reader with no embedded experience |
| `docs/governance-mapping.md` | Control objectives mapped to implementation, with the honest limits |
| `docs/ipc-protocol.md` | Binary protocol, v0.3, both links |
| `docs/release-notes.md` | Release bodies for v1.0.0, v2.0.0 and v3.0.0 |
| `docs/build-log.md` | Decisions in the order they were taken, including the ones that were wrong |

---

## Repository metadata

**Description** (324 characters, GitHub allows 350):

> Governance controls enforced in circuitry, not policy, across five Arduino boards. No actuation without a prior audit entry, no audit entry unwitnessed by a board the host does not control, and a bistable relay in the motor supply that no software can close. 703 tests, 100% coverage, full stack runs in CI with no hardware.

**Topics:**

`ai-governance` · `physical-ai` · `edge-ai` · `arduino` · `functional-safety` · `iso-42001` · `eu-ai-act` · `nist-ai-rmf` · `audit-trail` · `tamper-evident` · `embedded-systems` · `robotics-safety` · `human-oversight` · `micropython` · `real-time-systems`

---

## Keeping this current

This file goes stale the moment a build step lands. Anything published from it inherits whatever was wrong here, so it is worth updating in the same commit as the change rather than afterwards. The figures most likely to move: the test count, the number of steps shipped, the board statuses, and the untested list, which will start shrinking once there is a bench.
