# Deployment Guide

**From bare metal to a running demonstrator.**

Version 2.0, 2026-08-19

This guide assumes nothing. If you have never flashed a microcontroller, never
used a serial port and have only ever met Python through a tutorial, you are
the reader this was written for. Every command is given in full. Where a step
can go wrong, the failure and its fix are written down next to it rather than
left for you to discover.

Read Part 0 before buying or wiring anything.

---

## Table of contents

- [Part 0: What you are building, and the safety rules](#part-0-what-you-are-building-and-the-safety-rules)
- [Part 1: Bill of materials](#part-1-bill-of-materials)
- [Part 2: Run it with no hardware at all](#part-2-run-it-with-no-hardware-at-all)
- [Part 3: Prepare your workstation](#part-3-prepare-your-workstation)
- [Part 4: Flash the Alvik](#part-4-flash-the-alvik)
- [Part 5: Flash the UNO R4 WiFi oversight node](#part-5-flash-the-uno-r4-wifi-oversight-node)
- [Part 6: Wire the latch relay](#part-6-wire-the-latch-relay)
- [Part 7: Set up the VENTUNO Q governance node](#part-7-set-up-the-ventuno-q-governance-node)
- [Part 8: Set up the UNO Q perception node](#part-8-set-up-the-uno-q-perception-node)
- [Part 9: First full run](#part-9-first-full-run)
- [Part 10: Verify the governance controls](#part-10-verify-the-governance-controls)
- [Part 11: Run it as a service](#part-11-run-it-as-a-service)
- [Part 12: Segment the network](#part-12-segment-the-network)
- [Part 13: Troubleshooting](#part-13-troubleshooting)
- [Appendix A: Command reference](#appendix-a-command-reference)
- [Appendix B: Glossary](#appendix-b-glossary)

---

## Part 0: What you are building, and the safety rules

### The system in one paragraph

Four Arduino boards, and a fifth in the architecture that has no firmware yet.
One watches the world through a camera and says what it sees. One decides
whether that justifies moving, writes the decision to a tamper-evident log, and
only then sends a command. One is a wheeled robot that executes the command,
but refuses any command that does not carry a valid log reference. The fourth
watches the third and can stop everything: it has the physical button, and it
holds a relay contact sitting in the robot's motor supply.

```
UNO Q 4GB          VENTUNO Q                Alvik
Perception    -->  Governance         -->   Robot body
                        |                        ^
                        | heartbeat +            | motor +V
                        | audit digests          |
                        v                        |
                   UNO R4 WiFi                   |
                   Oversight ---> Latch Relay ---+
                                  bistable contact
```

**The fifth board is the Nesso N1**, the out-of-band operator console. It is in
`docs/architecture.md` and in Part 1 below because the design depends on it,
and it is build step 13, which is not written. Nothing in this guide
needs it. Buy it when the step lands, not before.

### Why a relay and not a wire

Earlier versions of this rig ran a signal wire from the oversight board into a
kill-switch pin on the robot. It worked, and it had two faults worth
understanding before you wire anything, because they are the reason this part
of the build looks the way it does.

It **released when the oversight board lost power**. A safety control that
stops enforcing the moment its own board dies is enforcing nothing. The relay
here is bistable: it holds its contact position with no current at all, so
pulling the plug on the oversight board leaves the motors exactly as isolated
as they were a moment before.

It **needed the robot's cooperation**. The kill line worked only because the
robot's firmware chose to read that pin. Reflash the robot and the line meant
nothing: a governance control bolted onto the thing it was meant to govern. The
contact is in the motor supply. There is nothing for the robot to agree to.

### Four safety rules, before anything else

These are not boilerplate. A wheeled robot with motors will move when you do
not expect it to.

1. **Test on blocks first.** Prop the Alvik so its wheels are off the ground
   until you have seen the override work. Part 10 tells you when to put it
   down.
2. **Know where the override button is** before you power anything on. Put it
   somewhere you can hit without looking.
3. **Never bridge the relay contact to make a demo work.** If the wheels are
   not turning, the system is telling you something true. Read Part 13 rather
   than shorting the two motor-supply terminals together.
4. **The relay is a switch, not a fuse.** It interrupts the motor supply. It
   does nothing about the battery, which is still connected and can still
   deliver a short-circuit current. Disconnect the battery before you touch
   any of the motor wiring, every time.

### How long this takes

| Part | Time | Needs hardware |
|---|---|---|
| Part 2, software only | 20 minutes | No |
| Parts 3 to 6, flash and wire | 2 to 3 hours | Yes |
| Parts 7 to 9, boards up and talking | 2 to 3 hours | Yes |
| Part 10, verification | 30 minutes | Yes |

Do Part 2 first even if every board is on your desk. It proves your
software works before any hardware can confuse the picture.

---

## Part 1: Bill of materials

### Boards

| Item | Why | Notes |
|---|---|---|
| Arduino UNO Q 4GB | Perception node | Needs a camera, see below |
| Arduino VENTUNO Q | Governance node | Runs Linux, SQLite and the filter |
| Arduino Alvik | The robot body | Ships with an 18650 battery |
| Arduino UNO R4 WiFi | Oversight node and safety arbiter | Holds the relay |
| Arduino Nesso N1 | Out-of-band console | Build step 13. Not used by this guide. |

### Parts you will also need

| Item | Quantity | Why |
|---|---|---|
| Modulino Latch Relay (ABX00138) | 1 | The physical safety path. Bistable. |
| Qwiic cable | 1 | R4 to the relay module. Ships with the Modulino. |
| Opto-isolator, PC817 or equivalent | 2 | The two sense channels. See Part 6. |
| Resistor, 1 kΩ, 1/4 W | 2 | Series resistors for the opto LEDs |
| Momentary push button, normally closed (NC) | 1 | The override button. NC matters: see Part 6. |
| Momentary push button, normally open (NO) | 1 | The clear button |
| Jumper wires, male to male | 8 | Two buttons, two sense channels |
| Wire suitable for motor current, 22 AWG or thicker | short lengths | The break in the motor supply |
| USB-C cable, data-capable | 3 | Board interconnects |
| USB-C power supply | 2 | UNO Q and VENTUNO Q |
| Arducam IMX219 8 MP camera module | 2 | See the note below |
| Breadboard, half size | 1 | Optional, but easier than soldering |

**Charging cables are not data cables.** Many USB-C cables carry power only.
If a board does not appear as a serial device, suspect the cable first. This
wastes more beginner hours than any other single thing.

**On the cameras**: the Arducam IMX219 8 MP module, two of them, splayed for
roughly 120° of coverage. Both CSI ribbon adapters ship in the box, so there is
no cable to choose. What is *not* settled is whether the CSI path binds cleanly
on the Qualcomm camera pipeline; `docs/architecture-reconciliation.md` section
7.1 has the detail, and a USB UVC webcam on the same capture abstraction is the
fallback. You do not need a camera at all to complete this guide: the synthetic
frame source in Part 9 substitutes for one, and every governance control in
Part 10 can be verified without a lens.

### On the buttons

The override button is **normally closed**: pressing it *opens* the circuit.
That is the opposite of what feels natural, and it is deliberate. With a
normally closed button, a cut wire, a pulled connector or a failed switch all
read the same as a press. The system fails towards stopping. A normally open
button that fails silently leaves you with an override that does nothing, and
you would not find out until you needed it.

The clear button is normally open, because a failed clear button simply means
you cannot clear, which is the safe direction.

---

## Part 2: Run it with no hardware at all

Everything in this section runs on an ordinary laptop. No boards, no cables.
The mock peers are real implementations of the two microcontroller state
machines driven over Unix pseudo-terminals, so the software path you exercise
here is the one that runs on the rig.

### 2.1 Check your Python

You need Python 3.11 or newer.

```bash
python3 --version
```

If that prints 3.10 or lower, or "command not found":

- **Ubuntu or Debian**: `sudo apt update && sudo apt install python3.11 python3.11-venv git`
- **macOS**: install [Homebrew](https://brew.sh), then `brew install python@3.11 git`
- **Windows**: install [WSL2](https://learn.microsoft.com/windows/wsl/install)
  and follow the Ubuntu instructions inside it. The rest of this guide assumes
  Linux or macOS. Native Windows is not supported: the code uses Unix
  pseudo-terminals, which Windows does not provide.

### 2.2 Get the code

```bash
git clone https://github.com/thierrysays/governed-edge-ai.git
cd governed-edge-ai
```

### 2.3 Create a virtual environment

A virtual environment keeps this project's packages out of your system Python.
Skipping it is the second most common source of beginner trouble.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. It will stay that way until you
close the terminal or run `deactivate`. **Every command from here on assumes
the virtual environment is active.** If you open a new terminal, run
`source .venv/bin/activate` again.

### 2.4 Install the dependencies

```bash
pip install --upgrade pip
pip install -r linux-stack/requirements.txt
pip install -r audit-service/requirements.txt
```

### 2.5 Run the test suite

```bash
make qa
```

This runs the linter, the type checker, two security scanners and the full test
suite. It takes about a minute. You should see, at the end of each module:

```
Required test coverage of 98% reached. Total coverage: 100.00%
634 passed
```

and 99 passed for the audit service, 733 in total.

If `make qa` passes, your software is sound and any later problem is hardware
or wiring. That is worth the minute.

If it fails: check that the virtual environment is active, that
`python3 --version` is 3.11 or newer, and that both `pip install` commands
completed. The C++ parity tests skip with a message if you have no compiler,
which is fine; install `g++` (`sudo apt install g++`) to run them.

### 2.6 Run the whole stack with mocks

Three terminals. Activate the virtual environment in each.

**Terminal 1, the governance node:**

```bash
cd governed-edge-ai/linux-stack
PYTHONPATH=../audit-service python3 -m governance.ventuno_q_service \
    --alvik mock --supervisor mock --db /tmp/audit.db
```

You should see the audit session ID, a pty path for the mock Alvik, a pty path
for the mock oversight node, and `Listening for UNO Q on 0.0.0.0:9100`.

**Terminal 2, the perception node:**

```bash
cd governed-edge-ai/linux-stack
python3 -m perception.uno_q_service --source synthetic --host 127.0.0.1
```

Synthetic frames now flow into the governance filter.

**Terminal 3, look at the audit log:**

```bash
sqlite3 /tmp/audit.db \
  "SELECT id, detection_label, confidence, command, command_sent, stm32_ack FROM audit_log LIMIT 10;"
```

Every detection is on record. `command_sent = 1` marks the one command sent per
frame; `stm32_ack = 1` means the mock MCU accepted it. If `sqlite3` is not
installed: `sudo apt install sqlite3`.

Stop both services with Ctrl-C.

### 2.7 Run the audit dashboard

```bash
cd governed-edge-ai/audit-service
DB_PATH=/tmp/audit.db python3 -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/docs> in a browser for the interactive API, or
<http://127.0.0.1:8000/events> for the raw event list.

You now have the full software stack working. Everything from here adds
hardware to a system you have already seen run.

---

## Part 3: Prepare your workstation

### 3.1 Install the Arduino tooling

You need `arduino-cli` for the R4, and `mpremote` for the Alvik.

```bash
# arduino-cli
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
sudo mv bin/arduino-cli /usr/local/bin/
arduino-cli version

# Board support and library for the UNO R4 WiFi
arduino-cli core update-index
arduino-cli core install arduino:renesas_uno
arduino-cli lib install "Arduino_LED_Matrix"

# MicroPython tooling for the Alvik
pip install mpremote
```

If you prefer a graphical tool, the [Arduino IDE
2.x](https://www.arduino.cc/en/software) does everything `arduino-cli` does.
The commands below give the IDE equivalent where it differs.

### 3.2 Get permission to use serial ports

On Linux, serial devices belong to the `dialout` group. Without membership you
get `Permission denied` on every upload.

```bash
sudo usermod -a -G dialout $USER
```

**Log out and back in** for this to take effect. A new terminal is not enough.
Check with `groups | grep dialout`.

On macOS no group change is needed.

### 3.3 Learn to find your boards

Plug in one board at a time and run:

```bash
# Linux
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
# macOS
ls /dev/cu.usbmodem* 2>/dev/null
```

Note which path appears for which board. Plugging in one at a time is the only
reliable way to tell them apart, and getting this wrong means flashing the
wrong firmware to the wrong board.

`arduino-cli board list` also shows connected boards with their names.

The paths are not stable across reboots or replugging. Part 11 shows how to
give each board a fixed name.

---

## Part 4: Flash the Alvik

The Alvik runs MicroPython. Firmware is copied as files, not compiled.

### 4.1 Check the Alvik's MicroPython

Connect the Alvik by USB-C, switch it on, then:

```bash
mpremote connect /dev/ttyACM0 exec "import arduino_alvik; print('Alvik library present')"
```

If that errors, follow Arduino's [Alvik getting started
guide](https://docs.arduino.cc/tutorials/alvik/getting-started/) to install
MicroPython and the `arduino_alvik` library first. Come back when the command
above prints its message.

### 4.2 Copy the firmware

```bash
cd governed-edge-ai/alvik-firmware
mpremote connect /dev/ttyACM0 fs cp ipc_codec.py :ipc_codec.py
mpremote connect /dev/ttyACM0 fs cp motor_map.py :motor_map.py
mpremote connect /dev/ttyACM0 fs cp main.py     :main.py
```

`main.py` runs automatically at boot. Copying it is the last step, so the board
does not start running a half-installed firmware.

### 4.3 Confirm it is running

```bash
mpremote connect /dev/ttyACM0 fs ls
```

You should see all three files. Reset the board (the button on the Alvik) and
it will start listening for commands over USB-C.

**The Alvik's D4 pin is a local test input**, active low, and it is not a
governance control. Nothing in this build wires anything to it. It exists so
you can stop the motors from the robot's own firmware while you are working on
that firmware. The control that matters is the relay contact in the motor
supply, which the Alvik cannot see, read or refuse. That is Part 6, and it is
why the wheels stay on blocks until then.

---

## Part 5: Flash the UNO R4 WiFi oversight node

### 5.1 Compile

```bash
cd governed-edge-ai
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi r4-supervisor
```

The sketch folder contains `.cpp` files alongside the `.ino`; the compiler
picks them all up. The `test/` subfolder is ignored, which is why the parity
harness lives there.

Expect a clean compile with no warnings. If you see `Arduino_LED_Matrix.h: No
such file`, the library install in Part 3.1 did not run.

### 5.2 Upload

Connect the R4 by USB-C and find its port, then:

```bash
arduino-cli upload --fqbn arduino:renesas_uno:unor4wifi -p /dev/ttyACM1 r4-supervisor
```

In the IDE: open `r4-supervisor/r4_supervisor.ino`, select **Arduino UNO R4
WiFi** under Tools > Board, pick the port, and click Upload.

### 5.3 Confirm it is watching

Look at the 12x8 LED matrix. Within a second of boot you should see a **steady
rectangular outline**: the WATCHING glyph. That means the state machine is
running.

**Listen for the relay.** Within the same second the board sends the contact
to open, whether or not it was already there, and you should hear a distinct
click from the module if it is connected. No heartbeat has arrived yet, and a
governance tier that has not said anything has not earned the authority to move
a robot. The contact closes by itself when the VENTUNO Q starts talking in
Part 9, and not before.

If the relay is not connected yet, the matrix will settle on the split-bar
LATCH glyph after a second or two rather than the outline. That is correct: the
board asked for a position, could not observe one, and refuses to pretend. It
clears once Part 6 is wired.

The five glyphs:

| Glyph | Meaning |
|---|---|
| Rectangular outline | WATCHING. Nothing wrong. |
| Solid block | OVERRIDE. An operator pressed the button. |
| Broken bars | STALE. The governance tier stopped reporting. |
| Diagonal cross | ATTEST. The audit digest stream skipped or rewound. |
| Two split boxes | LATCH. The relay is not where it was told to be, or cannot be seen. |

---

## Part 6: Wire the latch relay

This is the longest part of the build and the only one that touches motor
wiring. Read it through before starting.

**Power everything off and disconnect the Alvik's battery.** The relay
interrupts the motor supply; it does not disconnect the battery, and the
battery is what can deliver a damaging current into a slipped screwdriver.

### 6.1 What you are building

Three separate things, and it is worth keeping them separate in your head.

1. **The control path.** A Qwiic cable from the R4 to the Modulino Latch Relay.
   This is how the R4 moves the contact.
2. **The power path.** The relay's contact, wired into the break you make in
   the Alvik's motor supply. This is what actually stops the robot.
3. **The observation path.** Two opto-isolators reporting back to the R4 where
   the contact really is. This is what stops the R4 from *believing* it
   stopped the robot when it did not.

The third is the part people leave out, and it is the part that turns a
mechanism into a control. A relay you cannot read is an assertion.

### 6.2 The control path

Plug the Qwiic cable from the R4's Qwiic connector into the Modulino Latch
Relay. That is the whole step: the cable carries power, ground and I2C, it only
fits one way, and no jumpers or resistors are involved.

The module answers at I2C address `0x2A`. If yours is on a different address,
change `LATCH_I2C_ADDR` in `r4-supervisor/latch.h` and reflash.

### 6.3 The power path

Find the wire carrying positive supply from the Alvik's battery to its motor
driver. Cut it. Connect the battery side to one contact terminal on the relay,
and the motor side to the other.

That is the entire physical safety argument, and it is worth pausing on why
it is arranged this way rather than any of the easier alternatives:

- **In the supply, not in a signal.** The robot has no pin to read and no
  firmware decision to make. Reflashing it changes nothing.
- **Normally open.** An unlatched relay leaves the contact open, so a module
  that has never been commanded is a module that is not letting the robot move.
- **Bistable.** The contact holds position with no coil current. Unplug the R4,
  reboot the VENTUNO Q, pull the Qwiic cable: the contact stays where the last
  command put it. This is the property the signal wire it replaces did not
  have, and it is why a power cut at the oversight board is no longer a way to
  un-isolate the motors.

Keep this wiring short and mechanically secure. It carries the full motor
current, and an intermittent joint here presents as a robot that stutters for
reasons nothing in the audit log explains.

### 6.4 The observation path, and why it is two channels

The R4 has to know where the contact actually is, and it must never mistake
"I cannot see it" for "it is open".

That is harder than it sounds with one wire. Whichever way you wire a single
sense input, one of its two readings is also what a broken wire produces. So
one contact position becomes indistinguishable from a fault, and if that
position happens to be *open*, a cut wire tells the R4 the motors are isolated
when in truth it knows nothing about them. That is the single most dangerous
sentence this system could say, so the sense is built so it cannot say it.

Two channels, wired to disagree with each other:

| Channel | Opto LED across | Lit when | R4 pin |
|---|---|---|---|
| A | The relay contact itself | The contact is **open** | D3 |
| B | The motor rail, downstream of the contact | The rail is **live**, so the contact is closed | D5 |

Exactly one of them should ever be lit. The R4 reads both:

| A | B | Reading |
|---|---|---|
| Lit | Dark | OPEN. Motors isolated, and observed to be. |
| Dark | Lit | CLOSED. Motor supply available. |
| Dark | Dark | UNKNOWN. Cut harness, dead opto, flat battery. |
| Lit | Lit | UNKNOWN. Shorted harness. |

UNKNOWN is never rounded up to isolation. The R4 latches an override, shows the
LATCH glyph, and tells the governance tier, which raises its own override
independently.

### 6.5 The sense connections

Each opto-isolator has an LED side (pins 1 and 2) and a transistor side
(pins 3 and 4). The LED side goes on the motor supply. The transistor side goes
to the R4. Nothing electrically crosses between them, which is why no shared
ground is needed for these two channels.

**Channel A, "the contact is open":**

| From | To |
|---|---|
| Battery-side contact terminal | 1 kΩ resistor, then opto A pin 1 (anode) |
| Opto A pin 2 (cathode) | Motor-side contact terminal |
| Opto A pin 4 (collector) | R4 D3 |
| Opto A pin 3 (emitter) | R4 GND |

The LED sits across the contact, so it sees the full battery voltage when the
contact is open and nothing at all when it is closed.

**Channel B, "the motor rail is live":**

| From | To |
|---|---|
| Motor-side contact terminal | 1 kΩ resistor, then opto B pin 1 (anode) |
| Opto B pin 2 (cathode) | Alvik battery negative |
| Opto B pin 4 (collector) | R4 D5 |
| Opto B pin 3 (emitter) | R4 GND |

Both R4 inputs use the internal pull-ups configured in `setup()`, so a channel
whose opto is dark reads high, and a channel whose opto conducts reads low. No
resistors on the R4 side.

### 6.6 The buttons

| From | To | Wire |
|---|---|---|
| R4 D2 | One leg of the NC button | Override button |
| Other leg of the NC button | R4 GND | Override button return |
| R4 D4 | One leg of the NO button | Clear button |
| Other leg of the NO button | R4 GND | Clear button return |

Internal pull-ups again, no resistors.

### 6.7 Check the override button before you trust it

With the R4 powered and the relay connected:

1. Press and hold the override button. The matrix should switch to the solid
   block within about 30 ms, and you should hear the relay click open.
2. Release it. **The block stays.** That is the latch, and it is correct.
3. Press the clear button. Nothing happens yet, because no heartbeat has ever
   arrived and the node will not conclude the governance tier is healthy on the
   strength of silence.

If step 1 does nothing, check the wiring, then check that the button really is
normally closed: with the button untouched, a multimeter on continuity should
beep between its legs, and stop beeping while pressed. A normally open button
in that slot gives you a permanently asserted override, which looks like a
broken board.

### 6.8 Check the observation before you trust that

Reconnect the Alvik's battery and switch the robot on, wheels still off the
ground. With the R4 running and no heartbeat arriving, the contact should be
open.

1. The matrix should show the **outline**, not the split bars. The outline
   means the R4 asked for open, looked, and saw open. Split bars mean it looked
   and could not agree with what it saw.
2. Unplug channel A's wire from D3. Within about 100 ms the matrix should
   switch to the split bars: both channels now read dark, which is UNKNOWN, and
   the R4 refuses to claim an isolation it can no longer observe.
3. Plug it back in and press clear. The outline returns.

Step 2 is the test that matters here. A rig that passes steps 1 and 3 but not 2
has a working relay and a decorative sense circuit, and it will tell you the
motors are isolated on the day they are not.

---

## Part 7: Set up the VENTUNO Q governance node

The VENTUNO Q runs Linux. Treat it as a small server.

### 7.1 First boot

Follow Arduino's setup for the board: connect a display and keyboard, or
connect over SSH once it is on your network. Log in and confirm you have a
shell.

### 7.2 Install what the stack needs

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git sqlite3
```

### 7.3 Deploy the code

```bash
sudo mkdir -p /opt/governed-edge-ai
sudo chown $USER /opt/governed-edge-ai
git clone https://github.com/thierrysays/governed-edge-ai.git /opt/governed-edge-ai
cd /opt/governed-edge-ai

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r linux-stack/requirements.txt
pip install -r audit-service/requirements.txt
```

### 7.4 Give the audit log its own storage

The audit log is the governance artefact. Put it somewhere a full OS disk
cannot take it out, ideally a separate device.

```bash
sudo mkdir -p /data
sudo chown $USER /data
```

If you have separate storage, mount it at `/data` and add it to
`/etc/fstab` so it survives a reboot.

### 7.5 Prove the deployment before adding hardware

```bash
cd /opt/governed-edge-ai
make qa
```

Same suite, running on the target. If it passes here, the board's Python is
sound.

### 7.6 Note the board's IP address

```bash
hostname -I
```

Write it down. The UNO Q needs it in Part 8. If it changes on every boot, set a
static lease on your router: chasing a moving IP address gets old fast.

---

## Part 8: Set up the UNO Q perception node

Same shape as Part 7. The UNO Q runs the camera and the models.

### 8.1 Install and deploy

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

sudo mkdir -p /opt/governed-edge-ai
sudo chown $USER /opt/governed-edge-ai
git clone https://github.com/thierrysays/governed-edge-ai.git /opt/governed-edge-ai
cd /opt/governed-edge-ai

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r linux-stack/requirements.txt
```

### 8.2 Check the camera, if you have one

```bash
ls /dev/video*
```

`/dev/video0` is device index 0, and so on. To confirm it produces frames:

```bash
sudo apt install -y v4l-utils
v4l2-ctl --device=/dev/video0 --all | head -20
```

No camera yet? Skip this. `--source synthetic` generates frames and every
governance control in Part 10 can be verified without a lens.

### 8.3 Models are optional

The perception service tries to load YOLO-X, MediaPipe and PoseNet, and falls
back to a stub backend for each one it cannot load. The fallback is announced
in the log at startup. Stubs produce realistic detections, so the governance
path is exercised either way.

Adding real models is a separate exercise and is not needed to get the
demonstrator running.

---

## Part 9: First full run

**Wheels off the ground.** Prop the Alvik on a box so its drive wheels spin
free.

### 9.1 Power-on order

Order matters, because each board fails safe when the one below it is absent.

1. **Alvik.** Switch on. It waits for commands.
2. **UNO R4 WiFi.** The matrix shows WATCHING and the relay contact is open, so
   the Alvik has no motor supply at all. Commands would be accepted and the
   wheels would not turn.
3. **VENTUNO Q.** Boot it, but do not start the service yet.
4. **UNO Q.** Boot it, but do not start the service yet.

### 9.2 Identify the two serial devices on the VENTUNO Q

Plug in the Alvik and the R4 one at a time:

```bash
ls /dev/ttyACM*
```

Write down which is which. Getting them the wrong way round means the
governance filter sends commands to the oversight node and heartbeats to the
robot, and nothing works in a way that is confusing to debug.

### 9.3 Start the governance service

On the VENTUNO Q:

```bash
cd /opt/governed-edge-ai/linux-stack
source ../.venv/bin/activate
PYTHONPATH=../audit-service python3 -m governance.ventuno_q_service \
    --alvik /dev/ttyACM0 \
    --supervisor /dev/ttyACM1 \
    --db /data/audit.db
```

Substitute your two device paths.

**Watch the R4 as this starts.** The contact closes within a second, as soon as
the first heartbeat lands, and you should hear it. The matrix stays on
WATCHING. If it switches to STALE, the heartbeat is not arriving; if it switches
to the split bars, the contact did not go where it was told or cannot be
observed. Both are in Part 13.

### 9.4 Start the perception service

On the UNO Q:

```bash
cd /opt/governed-edge-ai/linux-stack
source ../.venv/bin/activate
python3 -m perception.uno_q_service \
    --source synthetic \
    --host 192.168.1.50 \
    --port 9100
```

Substitute the VENTUNO Q's IP address. With a camera, use
`--source v4l2 --device 0` instead.

### 9.5 Watch it work

On the VENTUNO Q, in a second terminal:

```bash
watch -n 1 'sqlite3 /data/audit.db \
  "SELECT id, detection_label, confidence, command, command_sent, stm32_ack \
   FROM audit_log ORDER BY id DESC LIMIT 10;"'
```

Rows should be appearing. The Alvik's wheels should turn when a command with
`stm32_ack = 1` goes through.

If the wheels turn: the whole chain works. Leave the Alvik on its blocks for
Part 10.

---

## Part 10: Verify the governance controls

Do not skip this. An untested control is a claim, and the entire point of this
project is the difference between the two.

### Test 1: the override button stops actuation

1. With everything running and commands flowing, **press the override button**.
2. The matrix switches to the solid block, the relay clicks, and the wheels
   stop.
3. Check the log:

```bash
sqlite3 /data/audit.db \
  "SELECT id, command_sent, notes FROM audit_log ORDER BY id DESC LIMIT 5;"
```

Every row after the press should show `command_sent = 0` and a note containing
`OPERATOR_BUTTON`. The system is still recording everything it sees. It has
stopped acting on it.

**If the wheels do not stop, stop the demonstration and go to Part 13.**

### Test 2: the override latches

Release the button. The matrix stays on the solid block; commands stay
suppressed. It does not lapse because the button came back up.

### Test 3: clearing requires a physical act

Press the clear button on the R4. The matrix returns to the outline and
commands resume. Nothing you can type on the VENTUNO Q does this: the
governance tier has no message that clears an override, which is checked by
`test_no_message_type_clears_an_override` in the test suite.

### Test 4: losing governance stops the robot

1. On the VENTUNO Q, stop the governance service with Ctrl-C.
2. Within 2 seconds the matrix switches to **STALE** and the contact opens.
3. Restart the service. The R4 stays in STALE: the override latched and needs
   the clear button.

This is the control that matters most and is least visible. A governance
process that crashes does not leave a robot running on its last instruction.

### Test 5: the physical path works on its own

1. Clear the override so everything is running.
2. Unplug the **USB-C cable between the R4 and the VENTUNO Q**, leaving the
   Qwiic cable and the relay in place.
3. Press the override button. The R4 is now unable to tell the governance tier
   anything, and the wheels still stop, because the motor supply is open.

That is the two-path design: the soft veto travels on a link that could be cut
or forged, and the contact does not travel on a link at all.

### Test 6: enforcement outlives the enforcer

The test the signal wire this replaces would have failed.

1. Press the override so the contact is open and the wheels are stopped.
2. **Unplug the R4 entirely**, USB and Qwiic both. The oversight board is now
   dead and the relay has no connection to anything.
3. The wheels stay stopped. The contact is bistable: it holds its position with
   no current at all.
4. Power the R4 back up. It comes back not assuming anything, reads the contact
   and finds it open, and holds it there until a heartbeat arrives.

An earlier version of this rig used a driven GPIO line, and step 2 released it.
A safety control that stops enforcing when its own board loses power is not a
safety control, and this is the test that says so out loud.

### Test 7: the R4 will not claim an isolation it cannot see

1. With everything running, press the override. Contact open, wheels stopped,
   solid block on the matrix.
2. Unplug the channel A sense wire from D3.
3. Within about 100 ms the matrix switches to the split bars. Both channels now
   read dark, which is UNKNOWN, not OPEN.
4. On the VENTUNO Q, check that the governance tier noticed independently:

```bash
sqlite3 /data/audit.db \
  "SELECT id, ts, notes FROM audit_log WHERE notes LIKE '%LATCH%' \
   ORDER BY id DESC LIMIT 5;"
```

The motors are still isolated throughout this test. What changed is that the
system stopped being able to *prove* it, and said so rather than carrying on.
That distinction is the whole design.

### Test 8: the audit log detects tampering

```bash
cd /opt/governed-edge-ai/linux-stack
source ../.venv/bin/activate
PYTHONPATH=../audit-service python3 -c "
import sqlite3
from oversight.attestation import verify_database
conn = sqlite3.connect('/data/audit.db')
print(verify_database(conn).reason)
"
```

Then edit a row by hand, as an attacker with database access would:

```bash
sqlite3 /data/audit.db "UPDATE audit_log SET confidence = 0.01 WHERE id = 3;"
```

Recompute. Structural checks alone will still pass, because the row count is
intact. Reconciling against the digests the R4 witnessed is what catches it,
and that comparison needs the digests read back from the board. The mechanism,
its reach and its limits are described in `r4-supervisor/README.md`.

### Test 9: put it on the floor

Only now, and only if Tests 1 to 7 all passed. Repeat Test 1 with the Alvik on
the ground and your hand on the override button.

---

## Part 11: Run it as a service

Manual `python3 -m ...` commands do not survive a reboot or a closed terminal.

### 11.1 Give each board a stable device name

Device paths shuffle. Fix them with a udev rule. Get the serial numbers first:

```bash
udevadm info -a -n /dev/ttyACM0 | grep '{serial}' | head -1
```

Create `/etc/udev/rules.d/99-governed-edge-ai.rules`:

```
SUBSYSTEM=="tty", ATTRS{serial}=="YOUR_ALVIK_SERIAL", SYMLINK+="alvik"
SUBSYSTEM=="tty", ATTRS{serial}=="YOUR_R4_SERIAL",    SYMLINK+="oversight"
```

Reload and replug:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/alvik /dev/oversight
```

Now use `/dev/alvik` and `/dev/oversight` everywhere instead of `/dev/ttyACM*`.

### 11.2 A systemd unit for the governance service

Create `/etc/systemd/system/governed-edge-ai.service`:

```ini
[Unit]
Description=governed-edge-ai governance service (VENTUNO Q)
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/opt/governed-edge-ai/linux-stack
Environment=PYTHONPATH=/opt/governed-edge-ai/audit-service
ExecStart=/opt/governed-edge-ai/.venv/bin/python -m governance.ventuno_q_service \
    --alvik /dev/alvik \
    --supervisor /dev/oversight \
    --db /data/audit.db
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now governed-edge-ai
sudo systemctl status governed-edge-ai
journalctl -u governed-edge-ai -f
```

**Note what `Restart=on-failure` means here.** If the service dies and restarts,
the R4 will already have latched a STALE override, and it stays latched until
someone presses the clear button. That is the intended behaviour: an automatic
restart brings the software back, not the authority to move. Do not add a
mechanism that clears the override automatically.

### 11.3 The perception service on the UNO Q

Same pattern, `/etc/systemd/system/governed-edge-ai-perception.service`:

```ini
[Unit]
Description=governed-edge-ai perception service (UNO Q)
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/opt/governed-edge-ai/linux-stack
ExecStart=/opt/governed-edge-ai/.venv/bin/python -m perception.uno_q_service \
    --source synthetic --host 192.168.1.50 --port 9100
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 11.4 The audit dashboard

`/etc/systemd/system/governed-edge-ai-dashboard.service`:

```ini
[Unit]
Description=governed-edge-ai audit dashboard
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/opt/governed-edge-ai/audit-service
Environment=DB_PATH=/data/audit.db
ExecStart=/opt/governed-edge-ai/.venv/bin/python -m uvicorn dashboard.app:app \
    --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

The dashboard is read-only and unauthenticated, on the assumption that your LAN
is the boundary. Do not port-forward it to the internet. Part 12 replaces that
assumption with a rule, for installations that need one.

---

## Part 12: Segment the network

Optional. Everything up to here runs on a flat network, and for a bench rig
that is the right answer. This part is for an installation that stays up: a
permanent demonstrator, a site deployment, anything an auditor might be shown.

It exists for one reason. The project claims that audit evidence lives outside
the host that produces it, and today that claim rests on 64 digests retained in
the R4's SRAM and on the discipline of whoever runs the rig. A firewall can
express the same claim a second way, independently: **the governance host has
no route to its own archive.** It can still rewrite its local SQLite log. It
cannot reach the place where its earlier digests are kept.

That is the reasoning behind the latch relay, moved onto the network. A
prohibition that survives the compromise of the thing it constrains.

### 12.1 What you need

Any router with four physical Ethernet ports and a firewall you can write rules
for: OPNsense, pfSense, VyOS, or plain nftables on Debian. The reference build
for this guide is a fanless Alder Lake-N appliance with four Intel I226-V
2.5 GbE ports, an M.2 cellular modem for uplink failover, and a TPM. None of
that is required. Four ports and default-deny are required.

8 GB of RAM is enough for routing, NAT and a VPN. Add memory before you add an
inline IDS.

The decision segment carries two hosts, so it also needs a small unmanaged
switch.

**Use the physical ports, not VLANs on a trunk.** The hardware supports 802.1Q
and you should not use it here. Four functions, four ports: a physical segment
does not end up inside another one because of a mistyped tag, and the topology
can be verified by following a cable with your finger. This is a governance
argument rather than a technical constraint, and it is the kind that survives
staff turnover.

### 12.2 Port assignment

| Port | Segment | Subnet | Occupants |
|---|---|---|---|
| `igc0` | WAN | site DHCP | Uplink; automatic failover to the cellular modem |
| `igc1` | DECISION | 10.42.10.0/24 | VENTUNO Q `.10`, UNO Q `.20`, via the switch |
| `igc2` | CONSOLE | 10.42.20.0/24 | Wi-Fi access point for the Nesso N1 |
| `igc3` | EVIDENCE | 10.42.30.0/24 | Archive node, dashboard reader, admin workstation |

Give both nodes static leases. Device paths shuffle and so do DHCP addresses,
and the perception service is started with the VENTUNO Q's address on its
command line.

**Neither the R4 nor the Alvik appears in this table.** They are on USB serial
to the VENTUNO Q. That is what makes this plan acceptable: the oversight path
never traverses the router, so the router never becomes a common-mode failure
with the function it would otherwise carry. If the R4's optional Wi-Fi console
is ever enabled, it belongs on CONSOLE, and 12.5 explains why.

### 12.3 The flow matrix

Default deny in both directions. Log every denial.

| Source | Destination | Service | What the rule carries |
|---|---|---|---|
| UNO Q | VENTUNO Q `.10` | TCP 9100 | Perception frames. The only flow on the decision chain. |
| VENTUNO Q | Nesso (CONSOLE) | build step 13 | Verdict stream out |
| Nesso | VENTUNO Q `.10` | build step 13 | Signed HALT lift, inbound |
| EVIDENCE | VENTUNO Q `.10` | TCP 8000 | Dashboard, read-only by construction |
| EVIDENCE | VENTUNO Q `.10` | TCP 22 | Digest collection, pull only |
| DECISION | `igc1` gateway | UDP 123 | Local NTP. See 12.4. |
| **DECISION** | **EVIDENCE** | **any** | **Denied. This is the rule that carries the argument.** |
| DECISION | WAN | any | Denied. No silent model update, no exfiltration. |
| CONSOLE | WAN, EVIDENCE | any | Denied. The Nesso speaks only to the VENTUNO Q. |
| EVIDENCE | WAN | HTTP/S, DNS, NTP | Administration and updates |

The two Nesso ports are left unspecified because build step 13 is not written.
Fix them when it is, and add them here rather than opening the segment.

### 12.4 Time, which the audit log depends on

A segment with no route out has no route to a time server, and an audit log
with drifting timestamps is a weaker artefact than one without. Run NTP on the
router and serve it on `igc1`. The decision segment gets a coherent, local,
attributable time source without gaining a path to anything else.

If the installation needs traceable time, discipline the router's clock from
the cellular modem's GNSS where the module provides one, and record in the
commissioning notes which source was used.

### 12.5 The three rules that are governance

The rest of the matrix is hygiene. These three can be defended to an auditor,
and each maps to a control objective already claimed in the README.

**The evidence asymmetry.** EVIDENCE initiates towards DECISION; DECISION never
initiates towards EVIDENCE. The archive node pulls, it does not receive. A
compromised VENTUNO Q has no network path to the archive of its own past
digests. *Off-host tamper evidence, ISO 42001 Clause 9.1.*

**The decision segment is mute outward.** Neither the NPU nor the perception
stack needs the internet while running. Updating a model becomes an explicit
act with a temporary rule opened and closed again, which means it leaves a
trace. *Model supply chain integrity.*

**A future R4 console lives on CONSOLE, never on DECISION.** The architecture
calls the arbiter's optional Wi-Fi console the least satisfying part of the
design, and it is right: it is a network surface on the supervisor. If it is
ever enabled, the firewall must guarantee that the governance host cannot reach
it. A supervisor reachable by the thing it supervises has stopped being a
supervisor. *Independent oversight function, Clause 9.2.*

### 12.6 Verify the segmentation

Three more tests, in the spirit of Part 10. An untested rule is a claim.

#### Test 10: losing the decision segment stops the robot

1. Everything running, commands flowing, the contact closed.
2. **Unplug the cable between the UNO Q and `igc1`.** The governance service
   stays up. Only its supply of perception frames is gone.
3. Within 2 seconds the matrix switches to **STALE** and the contact opens.
4. Plug it back in. The R4 stays in STALE and needs the clear button.

This looks like Test 4 and is a different failure. Test 4 kills the governance
process. This one leaves it running and blinds it, which is the failure a
network introduces and the one an operator is most likely to cause by accident.

It works because `SupervisorLink.record()` emits the heartbeat, and `record()`
is called once per logged event. No frames means no records, which means no
heartbeat, which means the arbiter stops being reassured. **The heartbeat is a
side effect of governing.** A governance tier that has stopped governing stops
saying it is fine, without anyone having written a watchdog to make that true.

> **Do not add a background heartbeat thread.** It is the obvious way to make
> the oversight link look more robust, and it would keep the arbiter reassured
> while the governance tier sat blind. This test is what stands between the
> current behaviour and that regression. The unit tests do not close the gap:
> `test_record_emits_digest_then_heartbeat` proves that recording sends a
> heartbeat, and `test_mock_supervisor` proves the node latches on silence.
> Nothing yet proves this side falls silent when it stops recording.

#### Test 11: the governance host cannot reach the archive

1. On the VENTUNO Q:

```bash
nc -zv 10.42.30.20 22
```

It must fail. A refusal is acceptable; a timeout is better, since it tells a
prospective attacker less.

2. On the archive node, the other direction:

```bash
nc -zv 10.42.10.10 8000
```

It must succeed.

3. Confirm the firewall logged the denial in step 1.

A denial that leaves no record is half a control. The log entry is what turns
"it did not get through" into evidence that it tried.

#### Test 12: the console segment is not a safety dependency

1. Power down the access point, or unplug `igc2`.
2. Press the override button. The contact opens and the wheels stop.
3. Press clear. The override lifts.

Nothing on the safety path needed the console. The signed HALT lift is a
convenience over a link that can fail; the ARM button is the path that cannot.
Run this test again after build step 13 lands, when there is finally something
on that segment to lose.

### 12.7 What this part does not prove

Segmentation does not stop a compromised VENTUNO Q from rewriting its own
database between two reconciliations. It shortens the window. The digests
retained on the R4 remain the evidence, and this changes nothing about that.

An operator with physical access reconfigures the firewall. The TPM and the
Kensington lock raise the cost; they do not answer the objection. Treat the
firewall's configuration as an auditable artefact, keep it in version control,
and review its diff the way you would review a change to the governance filter.

The router is a single component and it is deliberately not watching anything.
If it dies, the decision segment goes with it and Test 10 describes what
happens next. That is the correct amount of authority for a component nobody
audited.

And the cellular failover keeps the installation reachable, which is a
convenience for the operator and an additional surface for everyone else. On a
demonstrator it earns its place. On anything carrying real consequence, decide
deliberately, and write down who decided.

---

## Part 13: Troubleshooting

### Nothing appears at `/dev/ttyACM*`

1. Try a different USB-C cable. Charging cables have no data lines, and this is
   the single most common cause.
2. `dmesg | tail -20` immediately after plugging in tells you whether the
   kernel saw the device.
3. Check `groups | grep dialout` on Linux, and log out and back in if you
   recently added yourself.

### `Permission denied: '/dev/ttyACM0'`

The `dialout` group change in Part 3.2 has not taken effect. Log out fully and
back in. Do not work around it with `sudo`: a service running as root to reach
a serial port is a worse problem than the one you started with.

### The matrix shows STALE and will not clear

The R4 is not receiving heartbeats. In order:

1. Is the governance service actually running?
   `sudo systemctl status governed-edge-ai`
2. Is `--supervisor` pointed at the R4 and `--alvik` at the Alvik, rather than
   the other way round?
3. Is the USB-C cable a data cable?
4. Was the R4 flashed with the sketch, or is it still running an old one?

You cannot clear a STALE override while it is stale, by design. Fix the
heartbeat first, then press clear.

### Commands are logged but the wheels never move

Look at `stm32_ack` in the audit log:

| Value | Meaning | Where to look |
|---|---|---|
| `1` | The Alvik accepted and executed | The relay contact, then mechanics: battery, motors, wheels |
| `0` | The Alvik refused | Confidence below its own gate, or a missing audit reference |
| `NULL` | No reply arrived | Serial link to the Alvik: wrong device, bad cable, firmware not running |

`stm32_ack = 1` with wheels that do not turn is the normal signature of an open
contact, and it is not a bug: the robot accepted a lawful command and had no
motor supply to execute it with. Check the matrix first.

### The matrix shows the split bars and will not clear

The relay is not where the R4 told it to be, or the R4 cannot see where it is.
In order:

1. **Is the Qwiic cable seated?** With no I2C reply the module register reads
   UNKNOWN and nothing can agree.
2. **Is the Alvik's battery connected and the robot switched on?** Both sense
   channels are powered from the motor supply. A flat or disconnected battery
   leaves them both dark, which is UNKNOWN, which is correct and not a fault in
   the R4.
3. **Are both sense channels connected, D3 and D5?** Unplug one deliberately
   and confirm the glyph appears; that at least proves the detection works.
4. **Are the optos the right way round?** An LED in backwards never lights, so
   its channel reads dark permanently and the pair is never complementary.
5. **Is the contact welded?** If the register says one thing and both channels
   insist on the other, the relay itself has failed. Replace the module. Do not
   work around this in software.

Pressing clear will not help while the disagreement persists: the R4
re-latches on the next poll, about 100 ms later. Fix the observation first.

For `NULL`: confirm with `mpremote connect /dev/alvik fs ls` that the firmware
files are on the board.

### `ModuleNotFoundError: No module named 'logger'`

`PYTHONPATH` is not set. The governance filter imports the audit logger from a
sibling directory. Every invocation needs:

```bash
PYTHONPATH=/opt/governed-edge-ai/audit-service
```

The systemd unit in Part 11 sets it for you.

### `Address already in use` on port 9100

A previous governance service is still running.

```bash
sudo lsof -i :9100
sudo systemctl stop governed-edge-ai
```

### The UNO Q cannot reach the VENTUNO Q

```bash
ping 192.168.1.50                       # from the UNO Q
nc -zv 192.168.1.50 9100                # is the port open
```

Check that the governance service is bound (`Listening for UNO Q on
0.0.0.0:9100` in its log) and that no firewall sits between the two boards.

### Everything worked yesterday and nothing works today

Device paths shuffled on reboot. Part 11.1.

### The relay clicks but the wheels never move

The contact is closing and the motor supply still is not reaching the driver.
Check the two joints you made in Part 6.3 with a multimeter, with the battery
disconnected. An intermittent joint there presents as a robot that stutters for
reasons nothing in the audit log explains.

### The override button does nothing

Confirm the button is normally closed, not normally open. With the button
untouched, a continuity test between its legs should beep; pressing it should
stop the beep. A normally open button wired here gives a permanently asserted
override, and the matrix will show the solid block from boot.

---

## Appendix A: Command reference

### Governance service (VENTUNO Q)

```bash
python3 -m governance.ventuno_q_service [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--listen` | `0.0.0.0` | Address to accept UNO Q connections on |
| `--port` | `9100` | TCP port for the UNO Q link |
| `--alvik` | `mock` | Alvik serial device, or `mock` |
| `--supervisor` | `mock` | R4 serial device, `mock`, or `none` |
| `--oversight-optional` | off | Do not treat a lost oversight link as an override. Bench only. |
| `--db` | `:memory:` | Audit database path |
| `--threshold` | `0.70` | Linux-side confidence gate |

### Perception service (UNO Q)

```bash
python3 -m perception.uno_q_service [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--source` | `synthetic` | `synthetic` or `v4l2` |
| `--device` | `0` | V4L2 device index |
| `--host` | `127.0.0.1` | VENTUNO Q address |
| `--port` | `9100` | VENTUNO Q port |
| `--fps` | `10` | Synthetic source frame rate |

### Useful audit queries

```bash
# Recent activity
sqlite3 /data/audit.db "SELECT id, ts, detection_label, confidence, command, \
  command_sent, stm32_ack FROM audit_log ORDER BY id DESC LIMIT 20;"

# Suppression rate
sqlite3 /data/audit.db "SELECT command_sent, COUNT(*) FROM audit_log \
  GROUP BY command_sent;"

# Everything the oversight node stopped
sqlite3 /data/audit.db "SELECT id, ts, notes FROM audit_log \
  WHERE notes LIKE '%oversight override%';"

# Events flagged for review
sqlite3 /data/audit.db "SELECT id, ts, notes FROM audit_log WHERE flag = 1;"

# Actions the hardware refused
sqlite3 /data/audit.db "SELECT id, detection_label, confidence FROM audit_log \
  WHERE stm32_ack = 0;"
```

### QA on any machine

```bash
make smoke       # fast sanity pass
make test        # full suite with coverage
make lint        # ruff
make typecheck   # mypy
make security    # bandit SAST plus pip-audit CVE scan
make qa          # all of the above
```

---

## Appendix B: Glossary

| Term | Meaning |
|---|---|
| **Actuation** | Anything that physically moves. Here, the Alvik's motors. |
| **audit_ref** | The audit log row ID carried in every command. A command without one is refused by the robot. |
| **Attestation** | Proving a record has not changed since it was written. Here, a chain of SHA-256 digests. |
| **Bare metal** | Starting from an unconfigured machine, no assumptions. |
| **Confidence gate** | A minimum model confidence, enforced separately by the governance node and the robot. |
| **CRC** | A checksum that detects corrupted messages on a serial link. |
| **Default deny** | A firewall posture where nothing is allowed unless a rule allows it. The opposite posture allows anything not explicitly refused. |
| **Fail closed** | When something breaks, stop. The opposite, fail open, keeps running. |
| **Flash** | Write firmware onto a microcontroller. |
| **GPIO** | A pin that can be read or driven high or low. |
| **Opto-isolator** | An LED and a light sensor in one package. Passes a signal with no electrical connection between the two sides. |
| **Hash chain** | Each entry's digest includes the previous one, so changing an old entry changes all later digests. |
| **Heartbeat** | A periodic "I am alive" message. Its absence is the signal. |
| **IPC** | Inter-processor communication. The binary protocol between boards. |
| **Antivalent** | Two sense channels wired to disagree. Any other combination means the observation failed, not that the thing moved. |
| **Bistable** | A relay that holds its contact position with no current. Its state survives losing power. |
| **Latch relay** | The bistable relay whose contact sits in the robot's motor supply. The physical stop. |
| **Latch** | A state that stays set after its cause goes away, until deliberately cleared. |
| **NC / NO** | Normally closed / normally open. An NC button opens the circuit when pressed. |
| **Oversight node** | The UNO R4 WiFi. Watches the governance tier and can stop it. |
| **Qwiic** | A four-wire connector carrying I2C, 3.3 V and ground. Only fits one way. |
| **pty** | A pseudo-terminal: a software object that behaves like a serial port. Used by the mocks. |
| **Segment** | A separate network with its own port and its own rules. Here, one physical port per function, so that a mistyped tag cannot merge two of them. |
| **Suppression** | A detection that was logged but produced no command. Deliberate, and on record. |
| **udev rule** | A Linux rule giving a device a stable name. |
| **Virtual environment** | A private set of Python packages for one project. |
| **Watchdog** | A timer that fires if something fails to check in. |
| **WAL** | Write-ahead logging. A SQLite mode letting readers work alongside a writer. |

---

## Where to go next

| Document | Contents |
|---|---|
| `README.md` | The project and its argument |
| `docs/architecture.md` | Full architectural and functional specification |
| `docs/ipc-protocol.md` | Binary protocol reference for both links |
| `docs/governance-mapping.md` | Control objectives mapped to implementation |
| `r4-supervisor/README.md` | The oversight node in detail |
| `docs/build-log.md` | Decisions taken, and why |
| `docs/state-of-play.md` | Where the project stands, and what is not true any more |

If something in this guide is wrong or incomplete, that is worth an issue on
the repository. A deployment guide only earns its place by being tried.
