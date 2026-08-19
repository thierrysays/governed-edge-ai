# Cowork Task: Arduino Store BOM for governed-edge-ai (Peripherals Only)

Version 2.0, 2026-08-19. Supersedes the four-board version.

## Context

I am building a Physical AI safety demonstrator called **governed-edge-ai**: governance controls enforced in circuitry rather than described in policy.

**Project repo (public):** https://github.com/thierrysays/governed-edge-ai

**All five main boards are already owned. Do not include them in the BOM:**

| Board | Role |
|---|---|
| Arduino VENTUNO Q (Qualcomm IQ8 NPU 40 TOPS + STM32H5, 16 GB RAM) | Decision path: perception, governance filter, audit journal |
| Arduino UNO Q 4GB (Qualcomm QRB2210 + STM32U585, dual ISP 13 MP, 4 GB) | Independent witness; disagreement forces a HALT |
| Arduino Alvik (ESP32-S3 + STM32F411, mobile robot) | The governed body |
| Arduino UNO R4 WiFi (Renesas RA4M1 + ESP32-S3, 12x8 LED matrix, Qwiic) | Safety arbiter, outside the command chain |
| Arduino Nesso N1 (ESP32-C6, 1.14" touchscreen, Wi-Fi 6 / BLE / LoRa) | Out-of-band operator console |

---

## Architecture

```
                       Nesso N1
                       out-of-band console
                       verdicts out · signed HALT lift back
                             ▲                    │
                             │                    ▼
UNO Q 4GB  ───────────► VENTUNO Q ──────────────────────► Alvik
witness         2.5GbE  decision path, revocable    USB   governed body
UVC webcam              perception · GovernanceFilter     motors · ToF · IMU
independent model       signed audit journal                    ▲
disagreement                    ▲                               │ motor +V
forces HALT       heartbeat +   │  reports only                 │
                  chain digests │                               │
                           UNO R4 WiFi                          │
                           safety arbiter                       │
                           E-STOP / ARM / ACK · annunciator     │
                                │ Qwiic I2C                     │
                                ├──► Latch Relay ───────────────┘
                                │    bistable · 0x2A
                                ├──► Distance  (safety envelope)
                                └──► Movement  (proof of stop)
```

**The one thing to understand before shopping.** The physical stop is not a signal wire into the robot. It is a bistable relay contact sitting in the Alvik's motor supply, driven from the arbiter's own Qwiic bus and read back on two opto-isolated sense channels. That is why this BOM has a relay, two optos and motor-gauge wire in it, and why it no longer has a kill-line jumper.

---

## What still needs to be purchased

### 1. Modulino Latch Relay: CRITICAL, the physical safety path

**Arduino Modulino Latch Relay, ABX00138** (bistable relay, HFE60/3-1HT-L2, Qwiic/I2C at `0x2A`).

Its normally-open contact goes in series with the Alvik's motor supply. Bistable matters: the contact holds position with no coil current, so the motors stay isolated through a power cut at every board in the rig. A driven GPIO line would release, which is the fault this part replaces.

**Quantity 1.** Confirm the I2C address and whether the register reports the *commanded* position or the *observed* one; the firmware assumes the pessimistic case and treats the register as a cross-check only, but it is worth knowing which is true.

Search terms: "Modulino Relay", "latch relay", "ABX00138".

### 2. Modulino Distance and Modulino Movement

Both attach to the same Qwiic bus on the arbiter, deliberately not on the decision host's bus.

- **Modulino Distance (ABX00102, VL53L4CD ToF)**: a safety envelope measured outside the vision pipeline. It also covers a real hole in the cameras, which cannot focus closer than 200 mm.
- **Modulino Movement (ABX00101, 6-axis IMU)**: proof that the robot actually stopped, read by something that is not the robot.

**Quantity 1 each.**

### 3. Qwiic cables

The arbiter drives three modules on one bus. Modulinos ship with a cable each, but confirm, and note lengths: the relay sits near the Alvik's battery compartment and the arbiter does not.

**Quantity: 3, plus one spare.** Search terms: "Qwiic cable", "JST-SH 4-pin".

### 4. Camera modules: SOURCED, do not search for these

Settled outside the Arduino store: **Arducam IMX219 8 MP**, two of them, from Kubii (EAN 3272496309692). Both CSI ribbon adapters ship in the box, 15-pin to 22-pin and 22-pin to 22-pin, so there is no adapter to buy.

One thing is still worth sourcing: **longer CSI ribbon cables**. The supplied ones are 15 cm, and the two cameras have to be splayed roughly 52° apart on a mount while reaching the VENTUNO Q. 30 cm to 50 cm, matching whichever connector the VENTUNO Q presents.

**Also worth having: one USB UVC webcam.** The witness UNO Q needs one anyway, and it is the fallback if the CSI path does not bind cleanly on the Qualcomm camera pipeline, which is an open question. Any UVC-class webcam.

### 5. Sense circuit parts for the arbiter

The arbiter reads the relay contact back on two opto-isolated channels, wired antivalent so that a broken wire reads as "cannot see" rather than as a position. Neither the Arduino store nor a Modulino covers this; any electronics supplier will.

| Part | Qty | Purpose |
|---|---|---|
| Opto-isolator, PC817 or equivalent | 2 | Channel A across the contact, channel B across the motor rail |
| Resistor, 1 kΩ, 1/4 W | 2 | Series resistors for the opto LEDs |
| Wire, 22 AWG or thicker | short lengths | The break in the motor supply. Carries full motor current. |

No resistors on the arbiter side: both inputs use the board's internal pull-ups.

### 6. Buttons for the arbiter

- **Momentary push button, normally closed (NC)**: the override button, wired to D2. Normally closed matters: a cut wire, a pulled connector or a failed switch then all read as a press, so the system fails towards stopping. If the Arduino store sells only NO buttons, say so explicitly. An NC switch may need to come from an electronics supplier, and substituting an NO one would silently defeat the fail-safe wiring, with the failure appearing only when the button was needed.
- **Momentary push button, normally open (NO)**: the clear button, wired to D4. A standard tactile switch is fine.
- **Jumper wires, male to male**: eight. Two button returns, two sense channels, spares.
- **Half-size breadboard**: optional, avoids soldering.

Search terms: "push button", "tactile switch", "normally closed switch", "jumper wires", "breadboard".

### 7. Interconnect cables and power

- **USB-C data cable, VENTUNO Q to Alvik**: check whether the Alvik ships with one.
- **USB-C data cable, VENTUNO Q to UNO R4 WiFi**: the oversight link, 921600 baud. Must carry data, not power only.
- **Ethernet, UNO Q to VENTUNO Q**: the design calls for 2.5 GbE rather than Wi-Fi. A USB-C Ethernet adapter if neither board has a port, plus a short Cat 6 patch lead.
- **Power supply for the UNO Q 4GB**: check the recommended USB-C PD wattage and whether one is included.
- **Power supply for the VENTUNO Q**: check whether an adapter is sold separately.
- **Alvik battery and charger**: 18650 Li-ion. Check whether it is pre-installed and whether a charger is needed. A **spare 18650** is worth having: the sense circuit draws from the motor supply, and bench work runs the battery down faster than driving does.

### 8. Storage for the audit journal

The audit journal is the governance artefact, and it should not share a disk with the operating system.

- **M.2 NVMe SSD** if the VENTUNO Q takes one: check the form factor and key.
- **MicroSD card** otherwise, if a slot is available.

### 9. Superseded: the GIGA Display Bundle

Earlier versions of this BOM proposed a GIGA R1 WiFi plus Display Shield as a physical audit dashboard. **The Nesso N1 replaces it** and is already owned. Do not price the GIGA bundle.

### 10. Other optional accessories

- **Camera mount** holding two modules at a fixed, repeatable splay. The splay angle defines the safety envelope, so a mount that shifts is a control that drifts silently. A 3D-printed bracket or a machined plate, not tape.
- **Mounting hardware or an enclosure** to fix the UNO Q and VENTUNO Q together for stable demos.

---

## Your Task

1. Go to **https://store.arduino.cc**
2. For each category above, find matching products and note name, SKU, price, and stock status
3. Pay particular attention to the **Modulino Latch Relay**: it is the critical part in this revision, and the build has no physical enforcement without it
4. Note the **Qwiic cable lengths** supplied with each Modulino
5. If an item is not available on the Arduino store, note it clearly so I can source it elsewhere. The optos, resistors and NC button are all expected to fall in that category.

---

## Deliverable

Produce a complete **Bill of Materials (BOM)** covering peripherals only:

| # | Product Name | SKU | Unit Price (EUR) | Qty | Total (EUR) | Purpose | Stock |
|---|---|---|---|---|---|---|---|
| 1 | Modulino Latch Relay | ABX00138 | ... | 1 | ... | Physical stop: contact in the motor supply | ... |
| 2 | Modulino Distance | ABX00102 | ... | 1 | ... | Safety envelope outside the vision pipeline | ... |
| 3 | Modulino Movement | ABX00101 | ... | 1 | ... | Proof of stop, read off the robot | ... |
| 4 | Qwiic cable | ... | ... | 4 | ... | Arbiter to the three modules, plus a spare | ... |
| 5 | CSI ribbon cable, 30 to 50 cm | ... | ... | 2 | ... | Cameras to the VENTUNO Q | ... |
| 6 | USB UVC webcam | ... | ... | 1 | ... | Witness node, and CSI fallback | ... |
| 7 | Opto-isolator PC817 | n/a | ... | 2 | ... | Antivalent contact sense | ... |
| 8 | Resistor 1 kΩ | n/a | ... | 2 | ... | Opto LED series resistors | ... |
| 9 | Momentary button, NC | ... | ... | 1 | ... | Override button (arbiter D2) | ... |
| 10 | Momentary button, NO | ... | ... | 1 | ... | Clear button (arbiter D4) | ... |
| 11 | Jumper wire set | ... | ... | 1 | ... | Buttons and sense channels | ... |
| 12 | USB-C data cable | ... | ... | 2 | ... | Oversight link, Alvik link | ... |
| 13 | Ethernet adapter + patch lead | ... | ... | 1 | ... | Witness to decision path | ... |
| 14 | Power supply: UNO Q 4GB | ... | ... | 1 | ... | Power | ... |
| 15 | Power supply: VENTUNO Q | ... | ... | 1 | ... | Power | ... |
| 16 | 18650 cell, spare | ... | ... | 1 | ... | Alvik motor supply | ... |
| 17 | M.2 NVMe or microSD | ... | ... | 1 | ... | Audit journal, off the OS disk | ... |
| | | | **TOTAL (required)** | | **€XX.XX** | | |
| | | | **TOTAL (with optional)** | | **€XX.XX** | | |

**Important notes to include:**

- If the Modulino Latch Relay is out of stock, say so prominently and name the lead time. Nothing else in this list substitutes for it.
- For the NC override button: if the Arduino store carries only normally open buttons, say so explicitly and suggest a supplier for a normally closed one.
- If the Alvik ships with a battery and cable, note "included" so I do not double-buy.
- Direct product URLs from store.arduino.cc for every item found there.
