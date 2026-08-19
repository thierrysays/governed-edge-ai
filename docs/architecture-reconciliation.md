# Architecture Reconciliation

**Five boards, one job each: a rationale, and the deltas from the current codebase.**

Version 1.0, 2026-08-19 · Status: **design only, nothing implemented**

---

## 1. Why this document exists

Two architectures describe this project and they are not the same one.

**The diagram**, *governed-edge-ai — Chaîne de contrôle de gouvernance* (Scénario C + Lot E, v1, 19 August 2026), organises the system into four paths: decision (software, revocable), safety (physical, non-bypassable), independent evidence, and human supervision. Physical enforcement is a bistable latch relay in the motor power line. Evidence and human supervision hang off a Modulino Hub on the VENTUNO Q Qwiic bus, with an Arduino Nesso N1 out of band.

**The codebase** at `v2.0.0` implements an Arduino UNO R4 WiFi as an oversight node holding a live GPIO line into the Alvik's kill-switch pin, with perception on the UNO Q and governance on the VENTUNO Q.

The target settled here is neither exactly: **all four existing boards plus the Nesso N1, with the Modulino Hub cluster removed.** Section 3 sets out why that configuration is the right one, section 4 assigns the roles, and sections 5 onward list every delta from the code as it stands.

Two findings from the current code feed directly into the rationale and are worth reading first: section 5, where the current wiring breaks the diagram's own design rule, and section 6, where the diagram's latch relay is strictly stronger than the GPIO line I built.

**A caveat on sources.** Part numbers and electrical details are read off the diagram image and from vendor pages. Check them against datasheets before ordering or wiring anything. Where the diagram is silent, this document says so rather than filling the gap.

---

## 2. The diagram as read

| Component | Part | Role in the diagram |
|---|---|---|
| Caméras IMX219 ×2 | 8 MP, MIPI CSI 0/1, 22-pin ribbon | Vision input to the VENTUNO Q |
| VENTUNO Q | ABX00181 | MPU Qualcomm IQ8, Ubuntu, NPU 40 TOPS: perception (YoloX, MediaPipe) → GovernanceFilter → signed audit journal |
| STM32H5 | on the VENTUNO Q, Zephyr RTOS | Safety arbiter, < 1 ms. Drives the latch relay over I2C SET/RESET, *hors du chemin Linux* |
| UNO Q 4GB | + UVC webcam via hub | **Témoin**: second observation, independent model. Disagreement forces a HALT. Ethernet 2.5 Gb. |
| SSD M.2 NVMe Gen4 | 64 GB eMMC + M.2 | Persistent audit journal |
| Alvik | ESP32-S3 + STM32F411 | Physical body. USB-A 3.0 → USB-C, CommandRequest / CommandAck |
| Modulino Latch Relay | ABX00138, HFE60/3-1HT-L2 | Bistable, NO contact in series with motor +V. I2C 0x2A, SET/RESET 50 ms |
| Modulino Hub | ABX00100 | 8 I2C addresses, carrying the five modules below |
| Modulino Distance | ABX00102, ToF VL53L4CD | Safety envelope, outside the vision pipeline |
| Modulino Movement | ABX00101, IMU LSM6DSOX | Physical proof of stop, non-repudiation |
| Modulino Buttons | ABX00110, 3× SPST | E-STOP · ARM · ACK |
| Modulino Pixels | ABX00109, 8 RGB LED | ALLOW / GATED / HALT |
| Modulino Buzzer | ABX00108, piezo | Alarm on refusal |
| Arduino Nesso N1 | TPX00227 | Out-of-band supervision. Battery touchscreen, Wi-Fi 6 / BLE / LoRa. Verdicts and HALT lift. |

### The two rules it states

> **Règle de conception :** tous les modules de gouvernance se branchent sur le VENTUNO Q, jamais sur l'Alvik — le composant gouverné ne produit pas sa propre preuve.

> **Pourquoi c'est un contrôle et non une assertion :** le relais est bistable, son état survit à la coupure d'alimentation ET au reboot côté Linux.

Both are better than what the code does. Both are kept. The first is extended in section 3.

---

## 3. Rationale for five boards without the Modulino Hub

### The observation that drives it

The Modulino Hub cluster and the UNO R4 WiFi are two answers to the same question, and the diagram and the codebase each picked one without noticing the other existed.

| Function | Diagram's answer | The R4 already has |
|---|---|---|
| E-STOP / ARM / ACK | Modulino Buttons ABX00110 | Two buttons wired, a third pin free |
| ALLOW / GATED / HALT | Modulino Pixels ABX00109 | 12x8 LED matrix, on board |
| Alarm on refusal | Modulino Buzzer ABX00108 | A piezo on any free pin, or the Nesso's |
| I2C fan-out | Modulino Hub ABX00100 | A Qwiic connector, native |
| Latching override state machine | Implied, unallocated | Built and tested |
| Digest retention off the decision host | Not allocated | Built and tested |

Five modules, a hub and a bus, to reproduce what one board does already. The R4 wins on parts count, and it wins on something more important.

### The stronger reason: which bus the governance modules sit on

The diagram's rule is that governance modules attach to the VENTUNO Q, never to the Alvik, because *le composant gouverné ne produit pas sa propre preuve*. That is right, and it does not go far enough.

The VENTUNO Q is not a neutral host. It runs the perception models, the GovernanceFilter and the audit journal. It is the decision path, the thing the diagram itself labels *révocable*. Hanging E-STOP, the annunciator and the evidence sensors off its Qwiic bus means a compromised or hung decision host sits on the same I2C segment as the controls that are meant to survive it.

**Extend the rule:** governance modules attach to a board that neither decides nor is governed. That is the R4, and only the R4.

```
Diagram                                This design
-------                                -----------
VENTUNO Q ── Qwiic ── Hub ── E-STOP    R4 ── Qwiic ── latch relay
     │                    ── Pixels     │              (+ evidence sensors)
     │                    ── Buzzer     │
     │                    ── Distance   ├── buttons  (E-STOP / ARM / ACK)
     │                    ── Movement   ├── matrix   (ALLOW / GATED / HALT)
     │                                  │
  decides AND owns the                  owns the governance bus,
  governance bus                        decides nothing
```

The move costs nothing and buys a property the diagram cannot state: **the decision host has no electrical path to the safety modules at all.** Not a policy, not a firmware gate. No wire.

### One job per board

| Board | Single job | Decides? | Enforces? | Observes? |
|---|---|---|---|---|
| UNO Q 4GB | Witness: independent second observation | no | no | yes |
| VENTUNO Q | Decision: perception, governance filter, audit journal | yes | no | yes |
| Alvik | Governed body: executes, and may refuse | no | self only | no |
| UNO R4 WiFi | Safety arbiter: latch, buttons, annunciator, digest witness | no | **yes** | yes |
| Nesso N1 | Out-of-band human supervision | no | via signed lift | no |

No board both decides and enforces. That sentence is the whole architecture, and it is checkable by looking at the wiring rather than by reading a policy.

### What removing the Hub costs, honestly

The Hub is an I2C expander and nothing more, so removing it costs nothing on its own. What would cost something is dropping the two modules that were doing real work:

- **Modulino Distance (ToF VL53L4CD)** gives a safety envelope **outside the vision pipeline**. Today `proximity_breach` comes from the pose model, so one model failure removes the proximity control along with the perception that should have caught it. A separate sensor is not correlated with a model bug.
- **Modulino Movement (IMU LSM6DSOX)** gives **proof of stop**. Today the log records that a stop was commanded and acknowledged by the MCU that was asked to stop. An IMU records that the robot stopped moving. That is the difference between an assertion and an observation, and it is the same difference the design rule exists to protect.

Both attach to the R4's Qwiic connector directly. **No Hub is needed for two modules**, and on the R4 they land on the governance bus rather than the decision host's, which is better than the diagram. Recommendation: drop the Hub, drop Buttons, Pixels and Buzzer as redundant with the R4, and **keep Distance and Movement**, wired to the R4.

If they are dropped as well, two controls in the mapping lose their implementation and `docs/governance-mapping.md` should say so rather than quietly leaving them listed.

### Where the Nesso N1 fits

It is the only board an operator holds. Battery, touchscreen, its own radios, sharing no component with the decision chain. Its job is to make governance state legible away from the bench and to carry an authenticated human decision back. Section 9.

### What this configuration does not resolve

The STM32H5 remains the diagram's safety arbiter and remains unwritten (section 12). Until it exists, the R4 is the arbiter in practice. That is a real difference from the diagram and section 13 addresses it.

---

## 4. Target architecture

```
                       Nesso N1  (TPX00227)
                       out-of-band human supervision
                       touchscreen · battery · Wi-Fi 6 / BLE
                       verdict stream · signed HALT lift
                              ▲                    │
                verdicts,     │                    │  signed lift
                chain heads   │                    ▼
   UNO Q 4GB ──────────► VENTUNO Q (ABX00181) ──────────► Alvik
   witness       2.5 GbE  decision path, revocable  USB   governed body
   UVC webcam             IQ8 · perception ·              ESP32-S3 + STM32F411
   independent model      GovernanceFilter ·              motors · ToF · IMU
   disagreement           signed audit journal                  ▲
   forces HALT            STM32H5 (Zephyr)                      │ motor +V
                                   ▲                            │
                     heartbeat,    │  reports only              │
                     digests       │                            │
                              UNO R4 WiFi                       │
                              safety arbiter                    │
                              E-STOP / ARM / ACK                │
                              12x8 annunciator                  │
                              64 retained digests               │
                                   │ Qwiic I2C                  │
                                   ├──► Latch Relay ────────────┘
                                   │    ABX00138 · bistable · 0x2A
                                   ├──► Distance  ABX00102  (optional)
                                   └──► Movement  ABX00101  (optional)
```

Read it for what is absent. Nothing travels from the VENTUNO Q to the R4 except reports. Nothing on the governance bus is reachable from the decision host. Nothing reaches the motor supply except a bistable contact.

---

## 5. Finding: the current wiring breaks the design rule

At `v2.0.0` the R4's hard kill line runs from its D3 into the Alvik's D4 kill-switch input, and the Alvik firmware reads that pin as one of its four gates. That is a governance module hanging off the governed component, with two consequences.

**The Alvik participates in its own restraint.** The kill line works only because `alvik-firmware/main.py` chooses to honour it. Firmware on the governed board is a software gate wearing a hardware costume: reflash the Alvik and the line means nothing. The latch relay needs no cooperation, because it removes the motor supply.

**The evidence of a stop comes from the stopped thing.** `stm32_ack = 0` is reported by the same MCU that refused the command. Modulino Movement, on the R4's bus, observes that the robot actually stopped.

**Migration.** Enforcement moves to the latch relay (section 6). The Alvik's `audit_ref != 0` and confidence gates stay as defence in depth: they cost nothing and fail safe. Its D4 pin reverts to an optional local test button, documented as *not* a governance control.

---

## 6. Finding: the latch relay is strictly stronger than a GPIO line

| Property | GPIO kill line (repo) | Bistable latch relay (target) |
|---|---|---|
| Enforcing board loses power | Line **releases**. Fails open. | State **survives**. Fails safe. |
| Linux reboot | Unaffected | State survives, explicitly |
| Needs the governed board to cooperate | Yes | No |
| Needs a shared ground to mean anything | Yes, and a floating line reads as noise | No, a contact in series |
| Re-arm | R4 clear button | ARM button, or a signed Nesso lift |

The design I built **fails open on power loss** and no test could have caught it: `MockR4Supervisor` models a state machine, not an electrical system. That is a gap in the test strategy as much as in the design, and it is the clearest argument for the relay.

**What replaces what.** `supervisor_kill_line()` returning a boolean becomes an I2C SET/RESET pair to address 0x2A with a 50 ms pulse. The latch's state is readable, so the system can tell whether the relay actually latched rather than assuming the command took. That read-back is a new control worth having: it closes the gap between *we commanded a stop* and *the stop happened*.

---

## 7. Delta register

Classes: **A** load-bearing · **B** substantive but contained · **C** documentation or configuration.

| # | Element | Target | Repo at v2.0.0 | Class | Section |
|---|---|---|---|---|---|
| D1 | Physical enforcement | Bistable latch relay in motor power, I2C from the R4 | Live GPIO into the Alvik kill pin | **A** | 6 |
| D2 | Governance bus host | The R4, which neither decides nor is governed | R4 wired to the Alvik | **A** | 3, 5 |
| D3 | Perception location | VENTUNO Q, alongside the GovernanceFilter | UNO Q, sent over TCP | **A** | 8 |
| D4 | UNO Q role | Witness; disagreement forces HALT | Primary perception node | **A** | 8 |
| D5 | Human override | E-STOP / ARM / ACK on the R4 | Override + clear on the R4 | **B** | 3 |
| D6 | Out-of-band supervision | Nesso N1, verdicts and signed lift | Absent | **A** | 9 |
| D7 | Audit journal integrity | Signed | Unsigned SHA-256 chain | **A** | 10 |
| D8 | Safety envelope | Modulino Distance on the R4, outside the vision pipeline | Pose-model `proximity_breach`, inside it | **B** | 3 |
| D9 | Proof of stop | Modulino Movement IMU on the R4 | Absent | **B** | 3 |
| D10 | Annunciation | R4 matrix, ALLOW / GATED / HALT, plus a buzzer | R4 matrix, four glyphs | **C** | 3 |
| D11 | STM32H5 firmware | Zephyr, < 1 ms arbiter | `rt-control/` empty; only `MockSTM32H5` | **A** | 12 |
| D12 | Audit storage | M.2 NVMe, separate from OS | Local SQLite | **C** | config |
| D13 | UNO Q ↔ VENTUNO Q | Ethernet 2.5 Gb | TCP over Wi-Fi | **C** | docs |
| D14 | Cameras | IMX219 ×2, MIPI CSI. **Sourced**: Kubii 8 MP module for Raspberry Pi | "Still to source", no Arduino-native CSI confirmed | **C** | 7.1 |
| D15 | Modulino Hub + Buttons, Pixels, Buzzer | **Removed**, redundant with the R4 | Absent | **C** | 3 |

### 7.1 Cameras: sourced, and what the specification implies

The camera is settled: the [Kubii 8 MP module for Raspberry Pi](https://www.kubii.com/fr/cameras-capteurs/3610-module-camera-8mp-pour-raspberry-pi-3272496309692.html) (EAN 3272496309692), an **Arducam IMX219**, two of them, matching the diagram's *IMX219 ×2*. That closes the longest-running open item in the project: `docs/build-log.md` has listed the camera as unsourced since the first commit and `docs/cowork-bom-arduino.md` still calls it the most critical unknown. Both need correcting.

**The ribbon question is closed.** The module ships with both cables: 15 cm CSI 15-pin to 22-pin (Pi 3/4) and 15 cm CSI 22-pin to 22-pin (Pi Zero, Pi 5). Whichever connector the VENTUNO Q presents, a cable is in the box.

| Specification | Value |
|---|---|
| Sensor | Sony IMX219, 8 MP |
| Stills | 3280 × 2464 |
| Video | 1080p30, 720p60, VGA90 |
| Interface | MIPI CSI-2, 2 lanes |
| Focal length / aperture | 3.04 mm, f/2.0 |
| Field of view | 62.2° horizontal, 48.8° vertical |
| Focus | Fixed, 200 mm to infinity |
| Shutter | Rolling |
| IR | Filter fitted, visible light only |

### What this means for the governance argument

Four properties have consequences beyond image quality, and three of them argue for keeping the Modulino Distance module recommended in section 3.

**Minimum focus is 200 mm.** Anything closer than 20 cm is out of focus. On a small mobile robot the dangerous zone is exactly that near field: a hand reaching in, an obstacle at the bumper. The primary observer is blind, or at least blurred, precisely where the risk is highest. The ToF module (VL53L4CD, roughly 1 mm to 1.3 m) covers that band and is unaffected by focus. This moves Distance from "nice to have" to "covers a known hole in the primary sensor".

**Rolling shutter.** Different rows of the frame are exposed at different instants. On a moving platform that produces skew, and it means a single frame does not represent a single moment. For a demonstrator this is acceptable; for a safety claim about *where something was when we decided to stop*, it is a real error term that no amount of model accuracy removes. It should be stated in the threat model alongside the other untested hardware claims rather than discovered later.

**62.2° horizontal field of view.** Narrow. Anything outside that cone does not exist to the primary observer, and the audit log will faithfully record that nothing was detected. Two cameras can be arranged for stereo depth on one 62° cone or splayed for roughly 120° of coverage, and those are different safety arguments. The diagram says CSI 0 and CSI 1 without saying which. It needs deciding, because the choice determines what the phrase "safety envelope" actually covers.

**Visible light only, with the IR filter fitted.** Perception degrades in low light, and a system that degrades silently is worse than one that fails loudly. The audit log already records confidence per detection, so a lighting collapse shows up as a confidence collapse, and the suppression rate already in the dashboard is the natural place to surface it. Worth an explicit threshold rather than leaving it to whoever reads the log.

**Fixed focus is a small win.** No autofocus hunting means no nondeterminism in the perception path, which is the right trade for a governance system.

**Cable length is 15 cm.** Both supplied ribbons are short, which constrains where the cameras can sit relative to the VENTUNO Q. Longer CSI ribbons are inexpensive; worth ordering with the modules rather than discovering the constraint during assembly.

### The risk that replaces the cable question

*Compatible Raspberry Pi OS Bullseye* is a claim about Broadcom hardware and the Pi camera stack. The VENTUNO Q is a Qualcomm IQ8 running Ubuntu, where camera capture goes through the Qualcomm CAMSS pipeline rather than the Pi's. IMX219 has a mainline kernel driver, which makes this plausible, and plausible is not the same as working: it needs a sensor driver bound through CAMSS and a device-tree entry describing the CSI lanes.

That is the real open item, and it is bigger than the cable ever was. It cannot be settled until the VENTUNO Q pinout and device tree are published, which puts it in the same holding pattern as section 12.

**Mitigation that costs nothing:** `perception/capture.py` already abstracts the frame source, with V4L2, synthetic and file backends. If CSI capture on the VENTUNO Q proves difficult, a USB UVC camera on the same abstraction keeps the rig running while the CSI path is sorted out. The witness UNO Q is specified with a UVC webcam anyway, so that path is already in the design.

---

## 8. The witness UNO Q: an entirely new control

The diagram demotes the UNO Q to **témoin** with its own webcam and its own model, and *un désaccord avec le témoin UNO Q force un HALT*.

Nothing in the codebase implements this. It is the largest single piece of new work and the most interesting, because it is a control most AI safety architectures lack: **two independent observers must agree before the system may act.**

Questions the diagram does not settle:

- **What counts as disagreement?** A different label, the same label at divergent confidence, or a detection present in one stream and absent from the other. The third is the common case and the hardest.
- **Over what window?** Frame-synchronous comparison needs a shared clock the diagram does not show. A sliding-window agreement over N frames is more tractable and less brittle.
- **What if the witness is silent?** Fail closed, by the same argument that governs the oversight link today.
- **Veto or vote?** A veto is simpler and safer. A vote implies a tie-break, which implies a third opinion.
- **How independent is the model?** Same architecture with different weights is much weaker than a different architecture. Independence is the entire value of the control.

Note that the diagram already builds sensor independence into the witness: the VENTUNO Q sees through two IMX219 modules on MIPI CSI, and the UNO Q sees through a **UVC webcam over USB**. Different sensor, different interface, different driver stack. A ribbon cable working loose, a CSI driver fault or an ISP misconfiguration takes out the primary observer and leaves the witness seeing. That asymmetry is deliberate and worth keeping when the webcam is chosen: a second IMX219 on CSI would be cheaper and much weaker.

**Migration.** `perception/uno_q_service.py` keeps its shape: capture, backends, transport. The receiving side changes: `GovernanceFilter` gains an agreement gate ahead of the confidence gate, and a disagreement becomes an audit row with `actor = 'oversight'`, which the schema already supports.

Effort: **large**, a step of its own.

---

## 9. Arduino Nesso N1

**Part:** TPX00227. ESP32-C6 RISC-V to 160 MHz, 512 KiB RAM, 16 MiB flash. Wi-Fi 6, BLE 5.3, 802.15.4, sub-GHz LoRa. 1.14" 240×135 capacitive touchscreen. IMU, IR transmitter, RGB LED, buzzer, three programmable buttons, 250 mAh battery. 18 × 45 mm. Grove and Qwiic.

### What it displays

A battery-powered screen lets an operator stand away from the rig and still read its governance state, which an LED matrix bolted to the bench cannot do. Proposed content in priority order:

1. State: ALLOW / GATED / HALT
2. Why, when gated or halted: the reason code, not just the state
3. Latch relay position, read back rather than inferred
4. Counters: events logged, commands sent, suppression rate
5. Audit chain head, truncated, so continuity is eyeballable
6. Witness agreement status, once section 8 exists

### Remote HALT lift: decided, and its cost

The relay note says a HALT can only be lifted by the ARM button, *jamais une décision logicielle*. The Nesso line says it carries *levée de HALT*. These contradict each other. **The decision is that the Nesso may lift remotely**, so the note's wording is amended and the risk is recorded rather than absorbed.

Proposed replacement wording:

> Un HALT ne peut être levé que par un geste humain authentifié : le bouton ARM, ou une signature du Nesso N1 vérifiée hors du chemin Linux. Jamais par une décision du chemin logiciel.

That keeps the real principle, which was never "physical" but "the software path does not decide for itself", and it makes the Nesso's authority explicit instead of contradictory.

**Design, so that a lift is a human gesture and not a network event:**

- **Asymmetric signature, not a shared secret.** Ed25519, private key on the Nesso, public key provisioned to the arbiter at pairing. A shared HMAC key would have to sit on the VENTUNO Q, and a compromised VENTUNO Q could forge a lift. With a public key it cannot. This matters more than the algorithm.
- **Verified by the arbiter, not by Linux.** The frame is relayed by the VENTUNO Q and verified by the R4 (later the STM32H5). Linux stays the revocable path.
- **Bound to one HALT episode.** Every HALT gets an episode ID; a lift names the episode it lifts, so a captured frame cannot be replayed against a later HALT.
- **Challenge and response.** The arbiter issues a nonce with the HALT notification; the lift signs it.
- **Monotonic counter**, rejected if it does not advance.
- **Confirmed on the device.** Signed only after a deliberate touch on the Nesso, not by a background process. The device is the second factor; the touch is the gesture.
- **Every lift is an audit row.** `actor = 'human_override'`, with episode ID, counter and key fingerprint in `notes`. A remote lift must be at least as traceable as a button press, and unlike a button press it can be.

**Accepted risk, for the threat model.** A remote lift means whoever holds the Nesso, or its private key, can re-arm a halted robot from outside the room. The physical ARM button requires presence; this does not. The mitigations make forgery hard and every lift attributable. They do not make the channel equivalent to presence. This is a deliberate trade of safety margin for operational reach and belongs in `docs/architecture.md` section 12, in those words.

**On the radio.** Wi-Fi 6 and BLE are short range. If the point is supervision from genuinely out of the room, LoRa is the radio for it, and a verdict stream plus an occasional signed lift is well inside what LoRa carries. Worth deciding rather than leaving the radio unused.

---

## 10. Signing the audit journal

The diagram says *journal d'audit signé*. The code has an unsigned SHA-256 chain, and the existing gap is already documented: the chain protects rows already witnessed, and a host controlling both the database and the witness link can forge a consistent chain going forward.

**Where the key lives decides what the signature is worth.** On the VENTUNO Q it proves only that the journal was written by that host, since compromising the host compromises the key. On the R4 or the Nesso it proves the row was witnessed by something the decision path cannot impersonate.

The Nesso needs a keypair for section 9 and already receives a digest stream. **Countersigning chain heads on the Nesso** is the economical answer: one key ceremony, one device, two controls.

---

## 11. Protocol impact

On top of the thirteen message types in `docs/ipc-protocol.md` v0.2:

| Direction | Message | Purpose |
|---|---|---|
| VENTUNO Q → R4 | `LATCH_SET` / `LATCH_RESET` | Request a relay state change. The arbiter decides. |
| R4 → VENTUNO Q | `LATCH_STATE` | Read-back of the actual contact position |
| R4 → VENTUNO Q, Nesso | `HALT_EPISODE` | Episode ID and nonce on entering HALT |
| Nesso → R4 | `LIFT_REQUEST` | Signed: episode ID, nonce, counter, signature |
| R4 → Nesso | `LIFT_VERDICT` | Accepted, or the reason it was refused |
| Witness → VENTUNO Q | `WITNESS_OBSERVATION` | Second-observer detections for the agreement gate |
| VENTUNO Q → Nesso | `VERDICT_STREAM` | State, counters, chain head, for the display |

The five existing oversight messages carry over to the Nesso link with little change, which is the main thing worth salvaging from the R4 work. `OVERRIDE_CLEAR` acquires a signature field and stops being unauthenticated.

---

## 12. The STM32H5, which does not exist

The diagram makes it the safety arbiter: Zephyr, sub-millisecond budget, owner of the I2C SET/RESET line, out of the Linux path. `rt-control/` has been an empty placeholder since the first commit, and everything the codebase does with the STM32H5 goes through a Python model over a pty.

It is also blocked on the VENTUNO Q pinout, which `docs/build-log.md` has listed as open since day one.

**This is why the R4 earns its place rather than merely keeping it.** The R4 is the arbiter now, in hardware that exists, with a state machine that is written and tested and a parity harness that holds the C++ to a Python specification. When the pinout lands, either the arbiter moves to the STM32H5 and the R4 becomes the human interface, or it stays where it is and the STM32H5 handles only the < 1 ms motor-side work. That decision can wait; the rig does not have to.

Timing is the one claim that cannot be validated hardware-free. A < 1 ms arbitration budget is an assertion about a real MCU until measured.

---

## 13. Divergences from the diagram, stated plainly

This configuration is not the diagram. Three differences are deliberate and one is provisional.

| Difference | Deliberate? | Why |
|---|---|---|
| Governance bus on the R4, not the VENTUNO Q Qwiic | Yes | Section 3. The decision host should have no electrical path to the safety modules. |
| Modulino Hub, Buttons, Pixels, Buzzer removed | Yes | Redundant with the R4. Section 3. |
| Distance and Movement on the R4 rather than the Hub | Yes | Same bus argument, fewer parts |
| The R4 is the safety arbiter, not the STM32H5 | **Provisional** | Section 12. The STM32H5 is unwritten and pinout-blocked. |

The diagram should be reissued as v2 with the R4 and the Nesso in it, or this document should be linked from it, so that the published design and the built one do not drift apart again. That drift is what produced this document.

---

## 14. Proposed order of work

Each step is independently reviewable and leaves the tree green.

| Step | Work | Class | Depends on | Effort |
|---|---|---|---|---|
| 10 | Docs: publish this reconciliation, reclassify the R4, correct the delta | C | none | S |
| 11 | Latch relay driver, protocol and mock; retire the Alvik-side kill line | A | none | M |
| 12 | R4 as governance bus owner: Qwiic I2C layer, third button, ALLOW/GATED/HALT glyphs | A | 11 | M |
| 13 | Nesso N1: verdict stream, display, signed lift, key pairing | A | 11, 12 | L |
| 14 | Audit journal signing, countersigned by the Nesso | A | 13 | M |
| 15 | Distance and Movement: evidence outside the vision pipeline, proof of stop | B | 12 | M |
| 16 | Witness UNO Q and the agreement gate | A | none, parallel | L |
| 17 | STM32H5 Zephyr firmware, arbiter migration | A | pinout | L, blocked |

Steps 10 to 16 are testable hardware-free on the existing pattern: real state machines behind pty and I2C mocks, with a parity harness for anything that also exists in C. Step 17 is not, and its timing claims stay claims until there is hardware.

**One addition to the test strategy.** The GPIO line failing open on power loss was invisible because the mocks model logic, not electricity. The latch relay mock should model a power cycle explicitly, and a test should assert that the latch state survives one. Bugs of that class are found by modelling the failure, not by more coverage.

---

## 15. Open questions

Answers change the design, not just the wording. Several gate step 11.

1. **Scénario C, Lot E.** The diagram's subtitle names a taxonomy absent from the repository. What are the other scenarios and lots, and does this sit inside that scheme?
2. **Camera arrangement:** two IMX219 on one 62.2° cone for stereo depth, or splayed for roughly 120° of coverage? Different safety envelopes. Section 7.1.
3. **IMX219 on Qualcomm CAMSS:** does the VENTUNO Q device tree bind the mainline IMX219 driver? Gates the whole CSI path; UVC is the fallback. Section 7.1.
4. **Distance and Movement: keep or drop?** Section 3 recommends keeping both, wired to the R4, and section 7.1 strengthens the case for Distance. Dropping them removes two controls from `governance-mapping.md`.
5. **Latch state read-back:** polled, interrupt-driven, or on demand? Affects the arbiter's timing budget. Section 6.
6. **Witness disagreement:** what counts as disagreement, over what window, and does the witness hold a veto or a vote? Section 8.
7. **Witness model independence:** different weights, or a different architecture? Section 8.
8. **Nesso radio:** Wi-Fi 6 and BLE as drawn, or LoRa for genuine out-of-room range? Section 9.
9. **Signing key custody:** Nesso, arbiter, or both? Section 10.
10. **Arbiter migration:** does the R4 hand off to the STM32H5 when the pinout lands, or keep the role? Section 12.
11. **Movement module mounting:** on the Alvik, read by the R4? It reads as an exception to the design rule and is not one, because the Alvik has no path to alter what it reports. Worth stating explicitly. Section 3.

---

## 16. Decisions taken

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-19 | Target is five boards: UNO Q, VENTUNO Q, Alvik, UNO R4 WiFi, Nesso N1 | One job per board; no board both decides and enforces |
| 2026-08-19 | Modulino Hub, Buttons, Pixels and Buzzer removed | Redundant with the R4, which already has buttons, a matrix and a Qwiic port |
| 2026-08-19 | Governance modules attach to the R4, not the VENTUNO Q Qwiic bus | The decision host should have no electrical path to the safety modules |
| 2026-08-19 | The latch relay replaces the GPIO kill line into the Alvik | Bistable, survives power loss and reboot, needs no cooperation from the governed board |
| 2026-08-19 | Design document before implementation | The delta is large enough that building first would waste work |
| 2026-08-19 | The Nesso N1 **may** lift a HALT remotely | Operational reach, accepted against the loss of the presence requirement |
| 2026-08-19 | A remote lift is asymmetrically signed, verified off the Linux path, bound to one episode, and audited | A compromised VENTUNO Q must not be able to forge a lift |
| 2026-08-19 | The relay note is amended to *authenticated human gesture* | Resolves the contradiction without weakening the principle |
| 2026-08-19 | `v2.0.0` is held unpublished | It presents the R4 as a tier wired to the Alvik, which this supersedes |
| 2026-08-19 | Cameras sourced: Arducam IMX219 8 MP ×2 via Kubii | Matches the diagram; closes the oldest open item. Both ribbon adapters included. |
| 2026-08-19 | Modulino Distance is upgraded from optional to recommended | The IMX219's 200 mm minimum focus leaves the near field, where the risk is highest, blurred. The ToF covers that band. |

---

## 17. What this document does not do

It changes no code and orders no hardware. It does not settle section 15, and six of those questions gate step 11.

The camera's rolling shutter, its 62.2° cone and its low-light behaviour are named here but not yet written into `docs/architecture.md` section 12, where the untested hardware claims live. Step 10 does that.

It also does not re-examine whether the diagram's overall shape is right. That is taken as given; sections 5 and 6 argue for two specific choices within it, and section 3 argues for one departure from it.
