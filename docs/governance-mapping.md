# Governance Mapping

This document maps each control objective to its implementation in the codebase. Keep it in sync with actual code as the project develops.

## Framework references

| Abbreviation | Full name |
|---|---|
| ISO 42001 | ISO/IEC 42001:2023: AI management systems |
| COBIT APO | COBIT 2019, Align, Plan and Organise domain |
| COBIT DSS | COBIT 2019, Deliver, Service and Support domain |
| COBIT MEA | COBIT 2019, Monitor, Evaluate and Assess domain |
| NIST AI RMF | NIST AI Risk Management Framework 1.0 |
| TOGAF | The Open Group Architecture Framework, governance layer |
| IIA 3LM | Institute of Internal Auditors, Three Lines Model (2020) |

## Control mapping

| Control objective | Framework reference | Implementation | Status |
|---|---|---|---|
| Human oversight and override authority | ISO 42001 §6.1, COBIT APO12 | Physical NC button on the UNO R4 WiFi oversight node, outside the AI stack. Latches. No protocol message clears it. | Implemented, `r4-supervisor/` |
| Independent oversight function | ISO 42001 §9.2, COBIT MEA02, IIA 3LM line 3 | Tier 0 is not on the command path, uses its own link, and enforces through a relay contact no message can close | Implemented, `linux-stack/oversight/` |
| Auditability of automated decisions | ISO 42001 §9.1 | SQLite append-only log; schema in `audit-service/schema.sql` | Implemented |
| Tamper evidence held off-host | ISO 42001 §9.1, NIST AI RMF MEASURE 2.7 | SHA-256 hash chain over audit rows; 64 digests retained on a board the governance host does not control | Implemented, `oversight/attestation.py` |
| Witness before act | ISO 42001 §9.1 | The chain head reaches the oversight node before the command frame is written | Implemented, invariant 8 |
| Segregation of duties: recommend vs execute | COBIT APO01, TOGAF governance layer | The Linux side has no direct I/O path to actuators; all commands pass through the STM32 IPC channel | Architectural constraint |
| Physical stop, independent of software state | ISO 42001 §8.4, COBIT DSS02 | A bistable relay contact on the R4's Qwiic bus, in series with the Alvik's motor supply. No protocol in the path; survives a cut oversight cable and a compromised host. | Implemented, `oversight/latch.py`, needs bench verification |
| Enforcement that outlives its enforcer | ISO 42001 §8.4 | The contact is bistable and holds with no coil current. Cutting power to the oversight node does not restore motor power. | Implemented, `test_open_contact_survives_a_power_cycle` |
| Enforcement the governed component cannot defeat | COBIT APO01, IIA 3LM line 3 | The contact is in the supply, not in a pin the Alvik reads. Reflashing the governed board changes nothing. | Implemented |
| Control effectiveness observed, not assumed | ISO 42001 §9.1 | An antivalent sense pair observes the contact every 100 ms. Any reading that is not complementary is UNKNOWN, never isolation. | Implemented, `test_a_cut_harness_never_reads_as_isolated` |
| Governance-tier liveness | COBIT DSS02 | The oversight node latches an override if the governance tier stops reporting for 2 s. A software restart does not clear it. | Implemented |
| Fail-closed on loss of oversight | ISO 42001 §8.4 | Silence from the oversight node counts as a veto on the governance side | Implemented, `fail_closed` default |
| Model performance monitoring and drift detection | ISO 42001 §10.2 | Confidence-score time-series written to the audit log; flagging thresholds TBD | Partial: data captured, thresholds open |
| Data minimisation and local sovereignty | ISO 42001 §8.2, GDPR Art. 5(1)(c) | All inference runs on-device; no outbound telemetry by default. The R4's Wi-Fi console is off by default. | Architectural constraint |
| Visual governance status indicator | ISO 42001 §8.4 | 12x8 LED matrix on the R4: WATCHING, OVERRIDE, STALE, ATTEST, LATCH. Driven from the state machine, never from the link. | Implemented |
| Operator audit log access | ISO 42001 §9.1 | Local FastAPI service exposing the log over the LAN; no cloud relay | Implemented |
| Actor attribution | ISO 42001 §9.1 | Three actors in the log: `ai`, `human_override`, `oversight`. Machine-initiated supervisor action is distinguishable from a person pressing a button. | Implemented |

## Where the three lines model lands

Mapping the IIA Three Lines Model onto boards rather than departments is the clearest way to say what changed when the oversight node was added.

| Line | Function | Board |
|---|---|---|
| First | Doing the work, owning the risk | UNO Q perception, Alvik actuation |
| Second | Control and compliance built into the process | VENTUNO Q governance filter, audit log, dual confidence gate |
| Third | Independent assurance, reporting outside the line | UNO R4 WiFi oversight node |
| Third, out of band | Assurance reported to a person away from the rig | Nesso N1 console. Designed, build step 13, not yet written. |

Before the R4, this architecture had a strong second line and no third. The override and the kill switch were second-line controls presented as third-line assurance, which is the specific confusion that makes an oversight function unreliable in practice: it works exactly until the thing it supervises is the thing that failed.

The test that distinguishes them is not where a control sits on a diagram. It is whether the supervised function can switch it off. `test_no_message_type_clears_an_override` is that question asked in code.

The same test applied a second time moved the enforcement. A GPIO line from the third line into a pin on a first-line board is still third-line authority delegated to the party under review: it works only while the governed firmware keeps honouring it. Putting the contact in the motor supply removes the delegation. The first line has nothing left to comply with.

## Architecture: authority separation

```
                 +---------------------------------------------+
                 |      TIER 0: UNO R4 WiFi (oversight)        |
                 |                                             |
 Override  ----> |  Latching override state machine            |
 button (NC)     |  Heartbeat watchdog over the governance tier|
                 |  64 retained audit chain digests            |
                 |  12x8 LED matrix annunciator                |
                 |  Latch relay driver + antivalent read-back  |
                 |                                             |
                 |  Accepts no instruction from below.         |
                 |  Releasing an override is a physical act.   |
                 +--+---------------------------------------+--+
                    ^                                       |
    SUPERVISOR_     |                                       |  Qwiic I2C 0x2A
    HEARTBEAT   +   |                                       |  50 ms coil pulse
    ATTEST_DIGEST   |                                       v
                    |                    +------------------------------+
                    |                    |  Modulino Latch Relay        |
                    |                    |  bistable contact, normally  |
                    |                    |  open, in the motor supply   |
                    |                    +---------------+--------------+
                    |                                    |
                 +--+---------------------------------+  |  motor +V
                 |      TIER 2: Linux side (NPU)      |  |
                 |                                    |  |
 Camera -------> |  YOLO-X / MediaPipe / PoseNet      |  |
 (Tier 1)        |                                    |  |
                 |  Inference  -->  command request   |  |
                 |  Audit logger  (every inference)   |  |
                 |  Attestation chain  (every row)    |  |
                 +------------------+-----------------+  |
                                    |                    |
                       IPC over USB-C serial             |
                       RECOMMEND only, never EXECUTE     |
                                    |                    |
                                    v                    v
                 +---------------------------------------------+
                 |      TIER 3: STM32 (real-time, on Alvik)    |
                 |                                             |
                 |  audit_ref != 0 gate                        |
                 |  Independent float32 confidence gate        |
                 |  Control loop  (< 1 ms budget)              |
                 |                                             |
                 |  Motor driver -----> Motors                 |
                 |                      supply arrives, or     |
                 |                      does not, from above   |
                 +---------------------------------------------+
```

Read the diagram for what is absent as much as for what is present. Nothing travels upward from Tier 2 to Tier 0 except reports. Nothing on any board reaches the relay except Tier 0. And Tier 3 has no box for honouring the stop, because there is nothing there to honour: the supply either arrives at the motor driver or it does not, and that is decided outside the governed board entirely.

The one arrow Tier 2 does have to the relay is a request, not a control. It may ask for the contact to be opened, which Tier 0 always honours because more ways to stop are safe. It may ask for the contact to be closed, which Tier 0 refuses outright while an override stands.

## Honest limits

A control mapping that lists only what works is marketing. These are documented in full in `docs/architecture.md` section 12 and tested in `linux-stack/tests/test_security_oversight.py`.

| Limit | Consequence for the mapping above |
|---|---|
| The oversight serial link is trusted to the extent the USB-C cable is | An attacker with the cable can forge `OVERRIDE_CLEAR` and release the soft veto. The relay contact is unaffected, which is why the design has two paths rather than one. |
| The attestation chain is unkeyed | It detects tampering with rows already witnessed. A host controlling both the database and the link can forge a consistent chain over rows written after a compromise. |
| The governance filter does not block on `ATTEST_ACK` | An attestation fault stops the next command, not the one in flight. |
| Only 64 digests are retained | Reconciliation covers the window the oversight node still holds. |
| A fault in the dark sense channel is latent | Only the energised channel is under test at any instant, so a break in the other one is invisible until the contact next moves. Every command reads back, which is when it surfaces. |
| The relay's own I2C register may only echo the last command | It is treated as a cross-check, never as the observation. The sense pair is the source of truth. Both assumptions need a bench check against the module's datasheet. |
| Anyone with physical access to the motor wiring can bridge the contact | Physical enforcement assumes physical custody of the rig. No electronics fix this, and stating it is more useful than implying otherwise. |
| No hardware timing has been measured | Every figure in the protocol timing tables is a design target. |

## Open governance questions

- Sign the attestation digests with a key held only by the oversight node, closing the forward-forgery gap
- Define confidence-score thresholds that trigger a log flag rather than an automatic hold
- Decide audit log retention policy and whether rotation is permitted under the append-only constraint
- Decide whether an oversight override should ever clear other than by a physical act at the board (current answer: no)
- Define the read-back procedure for retained digests in a way that does not depend on the Wi-Fi console
