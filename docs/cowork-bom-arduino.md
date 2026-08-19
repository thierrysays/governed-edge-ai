# Cowork Task — Arduino Store BOM for governed-edge-ai

## Context

I am building a Physical AI safety demonstrator called **governed-edge-ai** on the **Arduino VENTUNO Q**.

**I already own:** the VENTUNO Q board.

**Project repo (public):** https://github.com/thierrysays/governed-edge-ai

**What the project does:**
- The VENTUNO Q has two processors: a Qualcomm Dragonwing IQ8 NPU (40 TOPS, running Ubuntu Linux) and an STM32H5 real-time co-processor (Zephyr OS)
- The Linux side runs an AI vision pipeline (object detection, gesture recognition, pose estimation) and a governance filter
- The STM32H5 side controls physical actuators (gripper open/close, halt)
- They communicate over UART with a binary IPC protocol
- The governance filter ensures no actuator command executes without a prior audit log entry

**What I need to add to make it work with real hardware:**
1. A camera for the NPU vision pipeline (object/gesture/pose detection)
2. A gripper or servo actuator controllable via the STM32H5 (the firmware uses GRIPPER_OPEN, GRIPPER_CLOSE, HALT commands)
3. Any required cables, shields, or adapters to connect the above to the VENTUNO Q
4. Power supply if not included with the board
5. Any official Arduino accessories designed for the VENTUNO Q ecosystem

## Your Task

1. Go to **https://store.arduino.cc**
2. Search for **VENTUNO Q** and visit its product page
3. Look for:
   - Compatible cameras (CSI, USB, or otherwise recommended for the board)
   - Compatible actuators, servo drivers, or gripper kits
   - Shields, breakout boards, or expansion boards for the VENTUNO Q
   - Recommended bundles or starter kits that include the VENTUNO Q peripherals
   - Power supply / USB-C adapter if listed
   - Any cables or connectors specifically listed for the VENTUNO Q
4. Also browse the **Shields**, **Modules**, and **Accessories** sections for anything compatible

## Deliverable

Produce a complete **Bill of Materials (BOM)** in this format:

| # | Product Name | SKU / Part Number | Unit Price (EUR) | Qty | Total | Purpose in Project |
|---|---|---|---|---|---|---|
| 1 | ... | ... | ... | 1 | ... | Camera input for NPU vision pipeline |
| 2 | ... | ... | ... | 1 | ... | Gripper actuator for STM32H5 control |
| ... | | | | | | |
| | | | **TOTAL** | | €XX.XX | |

Include:
- Direct product URLs from store.arduino.cc
- Note anything that is out of stock
- Note if a recommended camera or gripper is not available on the Arduino store (I may need to source from a third party)
- Note any technical compatibility caveats you find on the product pages

The VENTUNO Q board itself is already purchased — do not include it in the BOM.
