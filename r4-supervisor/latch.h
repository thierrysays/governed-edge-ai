/*
 * latch.h - Modulino Latch Relay, the physical safety path.
 *
 * A bistable relay (ABX00138, HFE60/3-1HT-L2) whose normally-open contact
 * sits in series with the Alvik's motor supply. This board opens it to
 * enforce a HALT and closes it to permit motion. Nothing else in the system
 * can reach it.
 *
 * Port of linux-stack/oversight/latch.py, which carries the tests.
 *
 * Two sources of truth, deliberately
 * ----------------------------------
 * `reported` is what the module's own MCU says over I2C. `observed` comes
 * from a sense circuit on the contact itself.
 *
 * They are separate because a state register on the module most likely echoes
 * the last command it accepted rather than observing where the contact sits.
 * Believing it would reproduce the error the read-back exists to remove: the
 * component that was told to stop reporting that it stopped. The sense line
 * is the truth, the register is a cross-check, and a disagreement between
 * them means a failed relay, a broken sense line, or a module lying.
 *
 * The observation is antivalent: two channels that must disagree with each
 * other, one energised only while the contact is open and the other only
 * while the motor rail is live. A single channel could not tell a position
 * from a cut wire, and one of the positions it would confuse with a fault is
 * OPEN, which reads as "the motors are isolated". Any non-complementary pair
 * is LATCH_UNKNOWN, and nothing here rounds that up to isolation. The wiring
 * is in r4_supervisor.ino; this file only ever sees the decoded position.
 *
 * Pure logic: no Arduino headers, no I2C calls, no time source of its own.
 * The caller supplies a LatchIo with the four operations that touch hardware,
 * which is what lets this compile on a host and be checked against the
 * Python model.
 *
 * Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#ifndef GOVERNED_EDGE_AI_LATCH_H
#define GOVERNED_EDGE_AI_LATCH_H

#include <stdbool.h>
#include <stdint.h>

/* I2C address from the diagram. */
#define LATCH_I2C_ADDR 0x2A

/* SET and RESET coil pulses, milliseconds. */
#define LATCH_PULSE_MS 50u

/* Sense-line read cadence. Polled rather than interrupt-driven: a contact
 * that silently failed to move raises no edge, and that is exactly the fault
 * worth catching. */
#define LATCH_POLL_INTERVAL_MS 100u

typedef enum {
  LATCH_OPEN = 0,     /* contact open, motor supply cut, HALT enforced */
  LATCH_CLOSED = 1,   /* contact closed, motor supply available */
  LATCH_UNKNOWN = 2   /* not read yet, or the sense line is unreadable */
} LatchPosition;

/* The four operations that touch hardware. Supplied by the sketch on the
 * board and by the harness on a host. */
typedef struct {
  void (*pulse_open)(void *ctx);
  void (*pulse_close)(void *ctx);
  LatchPosition (*read_register)(void *ctx);
  LatchPosition (*read_sense)(void *ctx);
  void *ctx;
} LatchIo;

typedef struct {
  LatchPosition commanded;
  LatchPosition reported;
  LatchPosition observed;
} LatchReading;

typedef struct {
  LatchIo  io;
  LatchPosition commanded;
  LatchReading last;
  bool     has_reading;
  uint32_t last_poll_ms;
  uint32_t transitions;
  uint32_t mismatches;
} Latch;

void latch_init(Latch *l, LatchIo io);

/* Open the contact: cut the motor supply. Idempotent. Reads back. */
LatchReading latch_enforce_halt(Latch *l, uint32_t now_ms);

/* Close the contact: make the motor supply available. Idempotent. Reads back. */
LatchReading latch_permit(Latch *l, uint32_t now_ms);

/* Read both sources now. */
LatchReading latch_poll(Latch *l, uint32_t now_ms);

/* Poll only if the cadence has elapsed. Returns true when it did. */
bool latch_poll_if_due(Latch *l, uint32_t now_ms, LatchReading *out);

/* True when all three positions agree and the observation is usable. */
bool latch_reading_agrees(const LatchReading *r);

/* True when the contact is *observed* open. False before the first poll and
 * false while the sense line is unreadable: neither is evidence that the
 * motors are isolated, and this must only ever claim safety it has seen. */
bool latch_enforcing(const Latch *l);

#endif /* GOVERNED_EDGE_AI_LATCH_H */
