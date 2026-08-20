# Architecture Reconciliation

**Five boards, one job each: a rationale, and the deltas from the current codebase.**

Version 1.2, 2026-08-19 · Status: **design only, nothing implemented**. All open questions now answered or defaulted; see sections 15 and 16.

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

Both attach to the R4's Qwiic connector directly. **No Hub is needed for two modules**, and on the R4 they land on the governance bus rather than the decision host's, which is better than the diagram.

**Decided:** the Hub, Buttons, Pixels and Buzzer are dropped as redundant with the R4. **Distance and Movement are kept.** Distance wires to the R4's Qwiic connector. Movement is more complicated than it looks, and section 3.1 works it out.

### 3.1 Movement, and the tether it implies

Proof of stop has two requirements that pull against each other.

- The IMU must be **on the Alvik**, because the Alvik's motion is the thing in question.
- It must be **read by something that is not the Alvik**, because the governed component must not report on itself. This is the entire point: the Alvik already carries an LSM6DSOX. What the Modulino adds is not a second sensor, it is a second **reader**.

Together those imply a wire from a moving robot to a fixed board, which on a wheeled robot is a tether.

**Decided: wireless telemetry**, so the Alvik keeps its mobility. That choice has a consequence that has to be designed rather than assumed.

#### The reader still cannot be the Alvik

If the Alvik reads the Modulino over I2C and relays the values over BLE, the control collapses. The governed component is then reporting its own motion, which is what its built-in IMU already does, and the Modulino becomes a second sensor rather than a second reader. Nothing is gained.

Two arrangements preserve the property:

**(a) A remote sensor head on the Alvik.** A small MCU that owns the Modulino Movement over I2C and reports to the R4 over BLE. It rides on the Alvik and belongs to the R4: no shared code, no shared firmware update path, no I2C address the Alvik can reach. Effectively the R4's sensor extended onto the robot by radio rather than by cable. Costs one more board and a pairing step.

**(b) Signed telemetry from a module the Alvik cannot forge.** Only works if the sensing element itself can attest to its readings, which a Modulino cannot. Not available with these parts.

So (a) is the arrangement, and it should be recorded as a sixth board rather than discovered during assembly. **New open question**, section 15.

#### A tether-free alternative worth considering first

The ToF Distance module, mounted on the R4 and pointed at the robot rather than outward, observes that the robot stopped moving without touching it: the measured distance stops changing. That is independent proof of stop, from a fixed vantage, with no tether, no radio and no extra board, and it comes from a module already being bought.

It is weaker in one respect, since it sees only motion along its axis and cannot detect rotation in place. It is stronger in another, since it does not depend on anything mounted on the robot at all, and a robot that has lost power still reports as stopped correctly.

It also has a hard range limit of roughly 1.3 m, beyond which the control does not degrade, it stops existing. Section 15.3 works out what that requires: a bounded demo area, and a third audit state so that "no observation available" never reads as "observed stopped".

**Decided: the ToF, with the range limit logged as its own state.** Five boards, no tether, no radio, and the remote sensor head held in reserve if rotation in place turns out to matter.

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

**Decided: polled at a fixed cadence** from the arbiter's main loop. Deterministic, no interrupt latency inside the timing budget, and it detects a latch that silently failed to change state rather than only catching transitions. The cadence becomes a documented parameter, and a mismatch between commanded and observed position is itself an audit event.

The read-back source matters more than the cadence, and section 15.2 works it out: a state register on the module's own MCU most likely echoes what it last commanded rather than observing where the contact is, which would reproduce the exact error the read-back exists to eliminate. **A GPIO sense line across the contact is the primary source; the I2C register is a cross-check, and disagreement between them is itself a finding.**

---

## 7. Delta register

Classes: **A** load-bearing · **B** substantive but contained · **C** documentation or configuration.

| # | Element | Target | Repo at v2.0.0 | Class | Section |
|---|---|---|---|---|---|
| D1 | Physical enforcement | Bistable latch relay in motor power, I2C from the R4 | **Closed at step 11.** Relay driver, protocol, mock and firmware port shipped | **A** | 6 |
| D2 | Governance bus host | The R4, which neither decides nor is governed | **Closed at step 11** for the relay; Distance and Movement remain at step 15 | **A** | 3, 5 |
| D3 | Perception location | VENTUNO Q, alongside the GovernanceFilter | UNO Q, sent over TCP | **A** | 8 |
| D4 | UNO Q role | Witness; disagreement forces HALT | Primary perception node | **A** | 8 |
| D5 | Human override | E-STOP / ARM / ACK on the R4 | Override + clear on the R4 | **B** | 3 |
| D6 | Out-of-band supervision | Nesso N1, verdicts and signed lift | Absent | **A** | 9 |
| D7 | Audit journal integrity | Signed | Unsigned SHA-256 chain | **A** | 10 |
| D8 | Safety envelope | Modulino Distance on the R4, outside the vision pipeline | Pose-model `proximity_breach`, inside it | **B** | 3 |
| D9 | Proof of stop | Modulino Movement IMU on the R4 | Absent | **B** | 3 |
| D10 | Annunciation | R4 matrix, ALLOW / GATED / HALT, plus a buzzer | R4 matrix, five glyphs since step 11 added LATCH. ALLOW / GATED / HALT and the buzzer are step 12. | **C** | 3 |
| D11 | STM32H5 firmware | Zephyr, < 1 ms arbiter | `rt-control/` empty; only `MockSTM32H5` | **A** | 12 |
| D12 | Audit storage | M.2 NVMe, separate from OS | Local SQLite | **C** | config |
| D13 | UNO Q ↔ VENTUNO Q | Ethernet 2.5 Gb | TCP over Wi-Fi | **C** | docs |
| D14 | Cameras | IMX219 ×2, MIPI CSI. **Sourced**: Kubii 8 MP module for Raspberry Pi | **Closed at step 10.** BOM, README and build log corrected | **C** | 7.1 |
| D15 | Modulino Hub + Buttons, Pixels, Buzzer | **Removed**, redundant with the R4 | Absent | **C** | 3 |

### 7.1 Cameras: sourced, and what the specification implies

The camera is settled: the [Kubii 8 MP module for Raspberry Pi](https://www.kubii.com/fr/cameras-capteurs/3610-module-camera-8mp-pour-raspberry-pi-3272496309692.html) (EAN 3272496309692), an **Arducam IMX219**, two of them, matching the diagram's *IMX219 ×2*. That closes the longest-running open item in the project: `docs/build-log.md` had listed the camera as unsourced since the first commit, and the peripherals BOM still called it the most critical unknown.

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

**62.2° horizontal field of view.** Narrow. Anything outside that cone does not exist to the primary observer, and the audit log will faithfully record that nothing was detected.

**Decided: splayed for roughly 120° of coverage.** A safety envelope is mostly about not missing things, and the near-field depth that stereo would have provided comes from the ToF module instead, which also covers the band the camera cannot focus on. The overlap region in the middle still gives coarse disparity if it is ever wanted.

Two consequences to write down. The splay angle becomes a **calibrated, documented parameter**, because the safety envelope is defined by it: change the mounting and the envelope changes silently. And the two cones leave a blind sector behind the robot, which the audit log cannot distinguish from an empty one. Both belong in the threat model.

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

**Decided:**

| Question | Answer |
|---|---|
| What counts as disagreement | **Absence.** A detection present in one stream and absent from the other. This is the common case, the hardest, and the one that matters: the primary observer missing something. |
| Over what window | **Sliding window of N frames**, not frame-synchronous. No shared clock is needed, and a single dropped frame does not halt the rig. N becomes a documented parameter. |
| Witness silent | **Fail closed**, by the same argument that governs the oversight link today. An observer that cannot be reached is not an observer that agrees. |
| Veto or vote | **Veto.** No tie-break rule, so no third opinion required. |

Choosing absence over label-mismatch has a consequence worth stating: the gate is asymmetric on purpose. The witness can stop the system by seeing something the primary missed, and it cannot start anything by agreeing. That is the right direction for a safety control, and it means a noisy witness costs availability rather than safety.

**Still open:** how independent the witness model actually is. Same architecture with different weights is much weaker than a different architecture, because correlated failures are exactly what this control exists to catch. Section 15.

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

**Decided: Wi-Fi 6**, as the diagram draws it. No extra hardware at the arbiter end, ample bandwidth for a verdict stream. The range is a room, so "out of band" here means off the decision path rather than out of the building, and the documentation should say that plainly rather than implying reach it does not have.

The link is built transport-agnostic regardless, so LoRa remains available later if range turns out to matter. Only the carrier would change; the signed-lift protocol is the same.

---

## 10. Signing the audit journal

The diagram says *journal d'audit signé*. The code has an unsigned SHA-256 chain, and the existing gap is already documented: the chain protects rows already witnessed, and a host controlling both the database and the witness link can forge a consistent chain going forward.

**Where the key lives decides what the signature is worth.** On the VENTUNO Q it proves only that the journal was written by that host, since compromising the host compromises the key. On the R4 or the Nesso it proves the row was witnessed by something the decision path cannot impersonate.

The Nesso needs a keypair for section 9 and already receives a digest stream. **Decided: the Nesso holds the only key**, and signs both chain heads and HALT lifts. One key ceremony, one device, two controls, and a signature that proves the row was witnessed by something the decision host cannot impersonate.

The cost is concentration. Lose the Nesso and both controls stop until a new device is paired, and the journal is unsigned in the interim rather than wrongly signed. That failure mode is loud, which is the right kind. It does argue for a documented re-pairing procedure and for keeping the previous public key on file so that historical signatures stay verifiable after a device is replaced.

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

**Decided: the R4 keeps the arbiter role permanently.** The STM32H5 handles only sub-millisecond motor-side work if and when it exists.

The reason is the same one that moved the governance bus in section 3, and it is easy to miss. The STM32H5 is out of the Linux path, which is not the same as being off the decision host: it sits on the VENTUNO Q's board, sharing a PCB, a power rail and in all likelihood a reset domain with the process it arbitrates over. The separation is a firmware boundary. The R4 is a separate board with separate power, so the separation is physical.

The trade is real and should be recorded as such. Arbitration on the R4 gives up the tightest achievable timing on the human and latch path. It buys a property that can be verified by looking at the wiring rather than by reading a datasheet, and for this project that is the more valuable of the two.

A useful consequence: the arbiter is no longer blocked on the pinout. Steps 11 through 15 can proceed on hardware that exists.

Timing is the one claim that cannot be validated hardware-free. A < 1 ms arbitration budget is an assertion about a real MCU until measured.

---

## 13. Divergences from the diagram, stated plainly

This configuration is not the diagram. Every difference below is deliberate and settled.

| Difference | Deliberate? | Why |
|---|---|---|
| Governance bus on the R4, not the VENTUNO Q Qwiic | Yes | Section 3. The decision host should have no electrical path to the safety modules. |
| Modulino Hub, Buttons, Pixels, Buzzer removed | Yes | Redundant with the R4. Section 3. |
| Distance and Movement on the R4 rather than the Hub | Yes | Same bus argument, fewer parts |
| The R4 is the safety arbiter, not the STM32H5 | Yes, now settled | Section 12. The STM32H5 is out of the Linux path but on the decision host's board; the R4 is a separate board with separate power. Physical separation over firmware separation. |
| Movement reported wirelessly rather than over the Qwiic bus | Yes | Section 3.1. Keeps the Alvik mobile, at the cost of needing a reader on the robot that is not the robot. |

The diagram should be reissued as v2 with the R4 and the Nesso in it, or this document should be linked from it, so that the published design and the built one do not drift apart again. That drift is what produced this document.

---

## 14. Proposed order of work

Each step is independently reviewable and leaves the tree green.

| Step | Work | Class | Depends on | Effort |
|---|---|---|---|---|
| 10 | Docs: publish this reconciliation, reclassify the R4, correct the delta | C | none | S. **Done** |
| 11 | Latch relay driver, protocol and mock; retire the Alvik-side kill line | A | none | M. **Done** |
| 12 | R4 as governance bus owner: Qwiic I2C layer, third button, ALLOW/GATED/HALT glyphs | A | 11 | M |
| 13 | Nesso N1: verdict stream, display, signed lift, key pairing | A | 11, 12 | L |
| 14 | Audit journal signing, countersigned by the Nesso | A | 13 | M |
| 15 | Distance and Movement: evidence outside the vision pipeline, proof of stop | B | 12 | M |
| 16 | Witness UNO Q and the agreement gate | A | none, parallel | L |
| 17 | STM32H5 Zephyr firmware, sub-ms motor-side work only | B | pinout | M, blocked |

Steps 10 to 16 are testable hardware-free on the existing pattern: real state machines behind pty and I2C mocks, with a parity harness for anything that also exists in C. Step 17 is not, and its timing claims stay claims until there is hardware.

With the arbiter staying on the R4, step 17 is no longer on the critical path. It went from blocking the architecture to being an optimisation of the motor-side timing, which is the main practical gain from that decision.

**One addition to the test strategy.** The GPIO line failing open on power loss was invisible because the mocks model logic, not electricity. The latch relay mock should model a power cycle explicitly, and a test should assert that the latch state survives one. Bugs of that class are found by modelling the failure, not by more coverage.

*Done at step 11, and it paid a second time.* Modelling the contact as an electrical thing rather than a boolean is what made the question "what does the sense line read when its wire is cut" askable at all. The answer was OPEN, which the arbiter would have reported as *the motors are isolated*. The observation is now an antivalent pair whose every failure decodes to UNKNOWN, and `SimulatedLatch` models both channels for the same reason: the failure worth testing is the harness, not the contact.

---

## 15. Proposed defaults

Eight questions were answered directly (section 16). One, the *Scénario C, Lot E* taxonomy, is dropped: the diagram's subtitle records it and nothing in this reconciliation depends on it.

The four below are reasoned defaults rather than answers. They are concrete enough to build on and cheap to overturn. Two of them turned up problems while being worked out, and those problems change the recommendation.

### 15.1 Witness model: a different family, tuned for recall

**Proposed: MobileNet-SSD or NanoDet on the witness, with a classical motion detector as a floor. Explicitly not a second YOLO variant.**

Correlated failure is the only thing that defeats a two-observer control, and architecture is where correlation lives. Two anchor-free one-stage detectors trained on similar data share inductive biases and therefore share blind spots; different weights do not fix that. A different detector family fails differently, which is the whole product being bought.

The design already has sensor independence working for it, and it is worth naming because it was not accidental: the primary sees through IMX219 on MIPI CSI, the witness through a UVC webcam on USB. Different sensor, different interface, different driver stack, and now different viewpoint. Model independence completes a set.

**The tuning matters more than the architecture, and follows from a decision already taken.** Disagreement means *absence*, and the witness holds a veto it can only use to stop. So a false positive on the witness costs availability, a false negative costs safety, and the two are not symmetric. The witness should be tuned for **recall over precision**: it should over-detect.

That inverts the usual instinct, and it has a useful consequence. A crude, high-recall method is both more independent and better suited than a second sophisticated model. Background subtraction or a motion-occupancy blob detector fails in ways no CNN fails, runs comfortably on the UNO Q, and over-detects by nature. Recommendation: run the classical method as the always-on floor and the small CNN above it, with the veto firing on the union.

Hardware agrees with the argument, which is a good sign. The UNO Q has no NPU to match the VENTUNO Q's 40 TOPS, so it could not run the same YOLO-X even if that were wanted.

### 15.2 Latch read-back: assume the register echoes the command, and sense the contact

**Proposed: a GPIO sense line across the relay contact as the primary read-back, with the I2C register as a secondary cross-check.**

The datasheet check is still owed. But the likely answer makes the original question the wrong one.

The Modulino line puts a small microcontroller behind the I2C interface. A bistable relay driven by SET and RESET pulses is a natural fit for that pattern, which means any state register the module exposes is most likely **the module's own record of what it last commanded**, not an observation of where the contact actually is.

If so, polling it proves nothing. It is the same error as `stm32_ack`: the component that was told to stop reporting that it stopped. Closing that gap is the entire reason the read-back exists, so a register that echoes the command would leave the design exactly where it started while looking like it had improved.

A sense line across the contact observes the physical position. It costs one GPIO on the R4 and one wire.

**Keep both, and treat disagreement as a finding.** If the module says latched and the contact says open, that is a high-value audit event: a failed relay, a broken sense line, or a module lying. Under this project's own logic, the case where two sources disagree is more informative than either source agreeing with itself.

### 15.3 Proof of stop: the ToF, and log its range limit as a state

**Proposed: Modulino Distance on the R4 pointed at the robot. No sixth board. And a new audit state for "no observation available".**

Section 3.1 already made the case: it needs no tether, no radio and nothing mounted on the robot, and a robot that has lost power still reports as stopped correctly. It misses rotation in place, which is the accepted cost.

**The problem found while specifying it:** the VL53L4CD reaches roughly 1.3 m. Beyond that the proof-of-stop control does not degrade gracefully, it simply stops existing, and a naive implementation would log "not moving" for a robot that had merely driven out of range.

That is the exact failure this project exists to make impossible: a control that silently reports success when it is not operating.

Two things follow. The demo area is bounded by the sensor, which should be stated rather than discovered. And the audit log needs three states where it would naturally have two:

| State | Meaning |
|---|---|
| `observed_stopped` | In range, distance stable. Proof of stop. |
| `observed_moving` | In range, distance changing. |
| `no_observation` | Out of range. **The control is not operating.** |

The same forensic distinction as `stm32_ack` being NULL rather than 0, and for the same reason: the absence of evidence must not read as evidence of absence.

### 15.4 Camera splay: 52° between optical axes

**Proposed: each camera rotated 26° from the centreline, giving about 114° of coverage with about 10° of overlap in the middle.**

Two 62.2° cones with their axes θ apart cover 62.2 + θ degrees and overlap by 62.2 − θ. At θ = 52° that is 114.2° of coverage and 10.2° of overlap.

Zero overlap would buy another 10° of width and is the wrong trade. Mounting tolerance on a bench rig is easily a couple of degrees per camera, so a seam specified as exactly closed becomes a real gap in practice, and a narrow blind wedge directly ahead of the robot is the worst possible place for one.

The overlap also pays for itself as a free self-test: an object in the shared wedge should be seen by both ISP channels, so a camera that has failed, been knocked or lost focus shows up as a disagreement between them rather than as silence.

**Blind sector: about 246°, behind and to the sides.** That belongs in the threat model in those words, because the audit log cannot tell an empty blind sector from an occupied one.

---

## 16. Decisions taken

### Configuration

| Decision | Rationale |
|---|---|
| Five boards: UNO Q, VENTUNO Q, Alvik, UNO R4 WiFi, Nesso N1 | One job per board; no board both decides and enforces |
| Modulino Hub, Buttons, Pixels, Buzzer removed | Redundant with the R4, which already has buttons, a matrix and a Qwiic port |
| Governance modules attach to the R4, not the VENTUNO Q Qwiic bus | The decision host should have no electrical path to the safety modules |
| Modulino Distance and Movement kept | The two Modulinos doing real work: a safety envelope outside the vision pipeline, and proof of stop |
| The latch relay replaces the GPIO kill line into the Alvik | Bistable, survives power loss and reboot, needs no cooperation from the governed board |

### Answered this round

| Question | Decision | Consequence to carry |
|---|---|---|
| Camera arrangement | **Splayed, roughly 120°** | Splay angle becomes a calibrated documented parameter; a blind sector behind the robot goes in the threat model |
| Distance and Movement | **Keep both** | Movement needs the arrangement in section 3.1 |
| Latch read-back | **Polled at fixed cadence** | Cadence is a documented parameter; commanded-versus-observed mismatch is an audit event. Needs a datasheet check. |
| Safety arbiter | **The R4, permanently** | Gives up tightest timing; buys physical rather than firmware separation. Unblocks steps 11 to 15 from the pinout. |
| Movement mounting | **Wireless telemetry** | Requires a non-Alvik reader on the robot, or the control collapses to self-reporting. Section 3.1. |
| Witness disagreement | **Absence, sliding window, veto, fail closed** | Asymmetric by design: the witness can stop, never start. A noisy witness costs availability, not safety. |
| Nesso radio | **Wi-Fi 6** | "Out of band" means off the decision path, not out of the building. Documentation should say so. |
| Signing key custody | **Nesso only** | Concentration risk accepted; needs a documented re-pairing procedure and retained old public keys |

### Proposed defaults, section 15

| Item | Default | Why it is not just a preference |
|---|---|---|
| Witness model | Different family, recall-tuned; classical floor plus small CNN | The veto is asymmetric, so over-detection costs availability and under-detection costs safety |
| Latch read-back | Antivalent opto-isolated sense pair primary, I2C register secondary | A register on the module's MCU most likely echoes the command, which is the error the read-back exists to eliminate. Two channels rather than one because a single pin makes a cut wire look like a position, and one of the positions it mimics is "isolated". |
| Proof of stop | ToF on the R4, plus a `no_observation` audit state | The sensor's 1.3 m range means the control stops existing rather than degrading; that must be on the record |
| Camera splay | 52° between axes, ~114° coverage, ~10° overlap | Mounting tolerance turns a zero-overlap seam into a blind wedge straight ahead; the overlap doubles as a camera self-test |

| Item | Status |
|---|---|
| Scénario C, Lot E | **Dropped.** Recorded in the diagram's subtitle; nothing here depends on it. |

### Standing

| Decision | Rationale |
|---|---|
| Design document before implementation | The delta is large enough that building first would waste work |
| The Nesso N1 **may** lift a HALT remotely | Operational reach, accepted against the loss of the presence requirement |
| A remote lift is asymmetrically signed, verified off the Linux path, bound to one episode, and audited | A compromised VENTUNO Q must not be able to forge a lift |
| The relay note is amended to *authenticated human gesture* | Resolves the contradiction without weakening the principle |
| Cameras: Arducam IMX219 8 MP ×2 via Kubii | Matches the diagram; both ribbon adapters included |
| `v2.0.0` is held unpublished | It presents the R4 as a tier wired to the Alvik, which this supersedes |

---

## 17. What this document does not do

It changes no code and orders no hardware.

Nothing now blocks step 10 or step 11. The defaults in section 15 are the author's judgement, not measurements, and three of them carry an assumption that a bench will settle quickly: that the latch module's register echoes its command, that the ToF reaches roughly 1.3 m in this mounting, and that mounting tolerance is a couple of degrees. Each is cheap to check and each changes only its own paragraph if wrong.

The camera's rolling shutter, its 62.2° cone and its low-light behaviour are named here but not yet written into `docs/architecture.md` section 12, where the untested hardware claims live. Step 10 does that.

It also does not re-examine whether the diagram's overall shape is right. That is taken as given; sections 5 and 6 argue for two specific choices within it, and section 3 argues for one departure from it.
