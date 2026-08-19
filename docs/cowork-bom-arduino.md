# Cowork Task: Arduino Store BOM for governed-edge-ai (Peripherals Only)

## Context

I am building a Physical AI safety demonstrator called **governed-edge-ai** using a three-tier distributed architecture.

**Project repo (public):** https://github.com/thierrysays/governed-edge-ai

**All three main boards are already owned: do not include them in the BOM:**
- Arduino VENTUNO Q (Qualcomm IQ8 NPU 40 TOPS + STM32H5, 16 GB RAM): Governance Brain
- Arduino UNO Q 4GB (Qualcomm QRB2210 + STM32U585, dual ISP 13 MP, 4 GB): Perception Node
- Arduino Alvik (ESP32-S3 + STM32F411, mobile robot): Physical Body

---

## Architecture

```
UNO Q 4GB  ──────────►  VENTUNO Q  ──────────►  Alvik
Perception node          Governance brain         Physical robot body
(cameras via ISP)        (NPU + audit log)        (motors + ToF + IMU)
Qualcomm QRB2210         Qualcomm IQ8 40 TOPS     ESP32-S3 + STM32F411
STM32U585                STM32H5                  wheels, sensors
```

Data flow: UNO Q 4GB captures camera frames → runs initial detection → sends DetectionResult to VENTUNO Q → GovernanceFilter logs + gates → sends audited CommandRequest to Alvik → Alvik executes (HALT / MOVE / etc.) → returns CommandAck or CommandReject.

---

## What Still Needs to Be Purchased (Peripherals Only)

### 1. Camera module(s) for UNO Q 4GB: CRITICAL

The UNO Q 4GB has **2× ISP at 13 MP / 30 fps**. Without a camera, the perception node has no visual input.

**Find on the Arduino store:**
- Any camera module officially listed as compatible with the UNO Q 4GB
- Look for: CSI ribbon cable camera, MIPI camera module, or any camera shield for UNO Q
- Search terms: "camera", "UNO Q camera", "CSI camera", "IMX", "OV"
- Need at minimum **1 camera** (ideally 2 to use both ISP channels: one for object detection, one for gesture/pose)
- Note if no camera is sold by Arduino: I will need to source from a third party (e.g. Arducam, Waveshare)

### 2. Interconnect cables

**Between UNO Q 4GB and VENTUNO Q:**
- If communicating over USB-C: standard USB-C to USB-C cable
- If communicating over UART/GPIO: UART TTL jumper cables or a small breakout

**Between VENTUNO Q and Alvik:**
- The Alvik connects via USB-C to the host board
- Need a USB-C cable (check if Alvik ships with one)

**Find on the Arduino store:**
- USB-C to USB-C cable (if sold)
- GPIO / jumper wire set
- Any ribbon cable for camera CSI

### 3. Power supplies

- **UNO Q 4GB:** check the recommended USB-C PD adapter wattage and whether one is included or sold separately
- **VENTUNO Q:** check if a power adapter is sold separately
- **Alvik:** runs on an 18650 Li-ion battery (check if pre-installed and if a charger is needed)

### 4. Arduino GIGA Display Bundle (optional — audit dashboard)

The GIGA R1 WiFi + GIGA Display Shield together provide a **physical audit log display** mounted on the demo rig. The GIGA R1 connects to the VENTUNO Q over Wi-Fi and polls the Flask audit dashboard API; the 3.97" touch display renders live governance events (detection type, confidence, audit_ref, HALT/MOVE decision, ACK/REJECT) without requiring a laptop.

- **GIGA R1 WiFi**: dual-core STM32H747 (Cortex-M7 480 MHz + M4 240 MHz), Wi-Fi + BT, USB-A host
- **GIGA Display Shield**: 3.97" IPS 480×800 capacitive touch, camera connector (Arducam-compatible), microphone

**Camera connector note**: the display shield's camera connector may accept the same Arducam MIPI modules sold for the UNO Q 4GB ISP. If so, the GIGA becomes a low-cost fallback perception node if the UNO Q camera is unavailable. Confirm compatibility when checking the Arduino store.

**Find on the Arduino store:**
- Search "GIGA Display Bundle" for the bundled SKU (cheaper than buying separately)
- Note individual SKUs for GIGA R1 WiFi and GIGA Display Shield if no bundle is listed
- Confirm whether the GIGA Display Shield camera connector accepts standard MIPI CSI modules

### 5. Other optional accessories

- **MicroSD card**: for persistent audit log storage beyond SQLite on the VENTUNO Q (if a slot is available)
- **Mounting hardware / enclosure**: to fix the UNO Q 4GB and VENTUNO Q together for stable demos
- **Ethernet adapter**: if Wi-Fi is not used for UNO Q ↔ VENTUNO Q communication (USB-C Ethernet dongle)

---

## Your Task

1. Go to **https://store.arduino.cc**
2. For each of the five categories above, find matching products and note name, SKU, price, and stock status
3. Pay particular attention to **camera compatibility with the UNO Q 4GB**: this is the most critical unknown
4. For the GIGA Display Bundle: confirm whether a bundled SKU exists and whether the display shield camera connector accepts standard MIPI CSI modules
5. If an item is not available on the Arduino store, note it clearly so I can source it elsewhere

---

## Deliverable

Produce a complete **Bill of Materials (BOM)** covering peripherals only:

| # | Product Name | SKU | Unit Price (EUR) | Qty | Total (EUR) | Purpose | Stock |
|---|---|---|---|---|---|---|---|
| 1 | Camera module (UNO Q ISP) | ... | ... | 1–2 | ... | Visual input for perception pipeline | ... |
| 2 | USB-C cable (UNO Q ↔ VENTUNO Q) | ... | ... | 1 | ... | Board interconnect | ... |
| 3 | USB-C cable (VENTUNO Q ↔ Alvik) | ... | ... | 1 | ... | IPC channel to robot | ... |
| 4 | Power supply: UNO Q 4GB | ... | ... | 1 | ... | Power | ... |
| 5 | Power supply: VENTUNO Q | ... | ... | 1 | ... | Power | ... |
| 6 | GIGA R1 WiFi (optional) | ... | ... | 1 | ... | Physical audit dashboard | ... |
| 7 | GIGA Display Shield (optional) | ... | ... | 1 | ... | 3.97" touch screen for audit log | ... |
| ... | | | | | | | |
| | | | **TOTAL (required)** | | **€XX.XX** | | |
| | | | **TOTAL (with optional)** | | **€XX.XX** | | |

**Important notes to include:**
- If no camera is available on the Arduino store: flag it and suggest the closest third-party alternative (Arducam MIPI, Raspberry Pi Camera Module 3, etc.) with approximate price
- If the Alvik ships with everything needed (battery, USB cable): note "included" so I don't double-buy
- For the GIGA Display Bundle: note if a bundled SKU exists (likely cheaper) and confirm MIPI camera connector compatibility
- Direct product URLs from store.arduino.cc for every item found there
