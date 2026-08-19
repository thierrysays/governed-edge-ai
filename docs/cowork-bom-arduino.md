# Cowork Task — Arduino Store BOM for governed-edge-ai (Three-Board Architecture)

## Context

I am building a Physical AI safety demonstrator called **governed-edge-ai** — a three-tier distributed architecture where AI governance is enforced across three Arduino boards, from camera to actuator.

**Project repo (public):** https://github.com/thierrysays/governed-edge-ai

**I already own:** the VENTUNO Q board. Do not include it in the BOM.

---

## Three-Board Architecture

```
UNO Q 4GB  →  VENTUNO Q  →  Alvik
(Perception)  (Governance)  (Actuation)
```

### Tier 1 — UNO Q 4GB: Perception Node
- Qualcomm QRB2210 (quad-core Cortex-A53, 2 GHz) + STM32U585 (Cortex-M33)
- **Dual ISP: 2× 13 MP cameras at 30 fps** — this is the camera input for the AI vision pipeline
- Runs object detection, gesture recognition, pose estimation on Debian Linux
- Sends `DetectionResult` objects to the VENTUNO Q over the network

### Tier 2 — VENTUNO Q: Governance Brain *(already owned)*
- Qualcomm Dragonwing IQ8 NPU (40 TOPS) + STM32H5
- Runs the GovernanceFilter: audit log → confidence gate → IPC command dispatch
- STM32H5 enforces dual-layer confidence gate; rejects any command without a valid audit reference

### Tier 3 — Alvik: Physical Body (Actuated Robot)
- Arduino Nano ESP32 + STM32F411 co-processor
- Mobile wheeled robot: motors, ToF 8×8 array, 6-axis IMU, color sensor, line follower
- Receives governance-approved commands (HALT, MOVE, etc.) via USB-C or UART
- Responds with CommandAck / CommandReject

---

## What to Buy

### Must-have (project cannot run without these)

1. **Arduino UNO Q 4GB** — the perception node
   - Needs cameras connected to its dual ISP
   - Check: does it ship with cameras, or are camera modules sold separately?

2. **Arduino Alvik** — the physical robot body
   - Check: does it include everything needed to run out of the box (battery, USB cable)?

### Cameras for UNO Q 4GB ISP

3. **Camera module(s) compatible with UNO Q 4GB ISP**
   - The UNO Q 4GB has 2× ISP at 13 MP / 30 fps
   - Look for: official Arduino camera modules, CSI ribbon cable cameras, or any camera shield listed as compatible with the UNO Q
   - Need at minimum 1 camera for the perception pipeline (object + gesture + pose detection)
   - Note if Arduino sells a camera add-on specifically for the UNO Q

### Connectivity between the three boards

4. **USB-C cable(s)** — VENTUNO Q ↔ UNO Q 4GB, VENTUNO Q ↔ Alvik (if USB used for IPC)
5. **UART/serial adapter** — if the boards communicate over UART rather than USB

### Power

6. **Power supply for UNO Q 4GB** — check recommended spec (USB-C PD adapter)
7. **Power supply for VENTUNO Q** — if not already covered by the board purchase

### Optional but useful

8. **Qwiic / I2C expansion cable** — UNO Q 4GB has a Qwiic connector; useful for adding sensors
9. **UNO shield** — any shield listed as compatible with UNO Q 4GB for prototyping
10. **Enclosure or chassis** — mounting the VENTUNO Q and UNO Q 4GB together for demos

---

## Your Task

1. Go to **https://store.arduino.cc**
2. Find and price the following items:
   - **Arduino UNO Q 4GB** (product page: https://store.arduino.cc/products/uno-q-4gb)
   - **Arduino Alvik** (product page: https://store.arduino.cc/products/alvik)
   - Any **camera module** compatible with the UNO Q 4GB ISP (search "camera", "CSI", "UNO Q")
   - Any **cables or adapters** listed as accessories for these boards
   - **Power supplies** if sold separately
3. Note stock status for each item
4. Note if any item must be sourced elsewhere (I will need to find alternatives)

---

## Deliverable

Produce a complete **Bill of Materials (BOM)** in this format:

| # | Product Name | SKU / Part Number | Unit Price (EUR) | Qty | Total (EUR) | Purpose | Stock |
|---|---|---|---|---|---|---|---|
| 1 | Arduino UNO Q 4GB | ... | ... | 1 | ... | Perception node — Tier 1 | In stock / Out of stock |
| 2 | Arduino Alvik | AKX00066 | ... | 1 | ... | Physical robot body — Tier 3 | ... |
| 3 | Camera module (UNO Q ISP) | ... | ... | 1–2 | ... | Vision input for perception pipeline | ... |
| 4 | USB-C cable | ... | ... | 2 | ... | Board interconnect | ... |
| ... | | | | | | | |
| | | | **TOTAL** | | **€XX.XX** | | |

Include:
- Direct product URLs from store.arduino.cc for each item
- Note any item not available on the Arduino store (needs third-party sourcing)
- Note any bundle or kit that covers multiple line items
- The VENTUNO Q is already owned — do not include it
