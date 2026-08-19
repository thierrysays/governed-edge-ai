/*
 * supervisor_state.h - oversight state machine for the Arduino UNO R4 WiFi.
 *
 * Pure logic: no Arduino headers, no I/O, no time source of its own. The
 * caller passes a millisecond clock in and moves the bytes. That is what
 * lets this file compile on a host and be checked against the Python
 * reference model in linux-stack/oversight/mock_supervisor.py, which is the
 * executable specification this is a port of.
 *
 * State machine
 *
 *   WATCHING --button press-------------------->  OVERRIDE
 *   WATCHING --heartbeat older than timeout---->  OVERRIDE
 *   WATCHING --digest gap or rollback---------->  OVERRIDE
 *   OVERRIDE --clear, only with the button released
 *              and a fresh heartbeat----------->  WATCHING
 *
 * The override latches. It does not lapse when its cause goes away, and no
 * inbound message can clear it: the governance tier has no way to talk its
 * own supervisor down. Releasing an override is a physical act at this board.
 *
 * Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#ifndef GOVERNED_EDGE_AI_SUPERVISOR_STATE_H
#define GOVERNED_EDGE_AI_SUPERVISOR_STATE_H

#include <stdbool.h>
#include <stdint.h>

#include "ipc_frame.h"

/* Retained digests. Matches DIGEST_CAPACITY_DEFAULT in the Python model.
 * 64 * 40 bytes = 2.5 KB of the RA4M1's 32 KB SRAM. */
#define SUP_DIGEST_CAPACITY 64

/* Silence from the governance tier beyond this latches an override. Several
 * times the 500 ms heartbeat interval, so one dropped frame does not halt
 * the rig. */
#define SUP_HEARTBEAT_TIMEOUT_MS 2000u

typedef enum {
  SUP_WATCHING = 0,
  SUP_OVERRIDE = 1
} SupervisorMode;

/* Glyph shown on the 12x8 LED matrix. */
typedef enum {
  ANN_WATCHING = 0,
  ANN_OVERRIDE = 1,
  ANN_STALE    = 2,
  ANN_ATTEST   = 3,
  ANN_LATCH    = 4
} Annunciator;

typedef struct {
  SupervisorMode mode;
  uint8_t  override_reason;      /* 0 while clear */
  bool     button_pressed;
  bool     chain_alert;

  uint64_t last_ref;             /* highest audit_ref accepted into the ring */
  uint32_t last_heartbeat_ms;
  bool     heartbeat_seen;
  uint32_t heartbeat_timeout_ms;

  /* Ring of digests held off the governance host. Oldest evicted first. */
  uint64_t ring_ref[SUP_DIGEST_CAPACITY];
  uint8_t  ring_digest[SUP_DIGEST_CAPACITY][IPC_DIGEST_BYTES];
  uint8_t  ring_head;            /* next slot to write */
  uint8_t  ring_count;

  /* Last heartbeat contents, for the status view. */
  uint64_t reported_audit_ref;
  uint32_t reported_events;
  uint32_t reported_commands;
  uint8_t  reported_state;

  uint32_t heartbeats_received;
  uint32_t digests_received;
  uint32_t overrides_asserted;
  uint32_t overrides_cleared;
  uint32_t chain_faults;
} SupervisorState;

void supervisor_init(SupervisorState *s, uint32_t heartbeat_timeout_ms, uint32_t now_ms);

/* Latch an override. Returns true only when this call is the one that
 * latched it, so the caller knows whether to transmit OVERRIDE_ASSERT.
 * The first reason wins: a later trigger does not relabel what stopped the
 * rig. */
bool supervisor_assert_override(SupervisorState *s, uint8_t reason);

/* Release a latched override. Returns false when refused: while the button
 * is still held, and until a heartbeat has actually arrived and is still
 * fresh. Clearing an
 * override whose cause is still present would put the rig straight back
 * into the state that raised it.
 *
 * Clearing an attestation override also resynchronises last_ref to what the
 * governance tier last reported, so the node can resume. The gap stays in
 * the retained ring; the clear records that an operator accepted it. */
bool supervisor_clear_override(SupervisorState *s, uint32_t now_ms);

void supervisor_on_heartbeat(SupervisorState *s, const SupervisorHeartbeat *hb,
                             uint32_t now_ms);

/* Verify one digest and file it. Returns the AttestVerdict to send back.
 * A verdict other than ATT_CHAIN_OK also latches an override. */
uint8_t supervisor_on_digest(SupervisorState *s, const AttestDigest *d);

bool supervisor_heartbeat_stale(const SupervisorState *s, uint32_t now_ms);

/* Call on every loop pass. Returns true when the heartbeat watchdog latched
 * an override on this call, meaning OVERRIDE_ASSERT must be transmitted. */
bool supervisor_tick(SupervisorState *s, uint32_t now_ms);

/* Debounced button input. `pressed_now` is the debounced level, active-low
 * already resolved by the caller. Returns true when a press latched an
 * override on this call. */
bool supervisor_set_button(SupervisorState *s, bool pressed_now);

Annunciator supervisor_annunciator(const SupervisorState *s);

/* True when the motors should be isolated: an override is latched, or no
 * heartbeat has ever arrived. The sketch drives the latch relay from this on
 * every pass. A governance tier that has said nothing has not earned the
 * authority to move a robot, and the relay releases on first contact with no
 * arming step.
 *
 * This states intent. Whether the contact actually moved is a separate
 * question, answered by latch_enforcing() against the sense line, and the
 * two differing is exactly the fault worth catching. */
bool supervisor_kill_line(const SupervisorState *s);

/* Read back a retained digest, oldest first. Returns false if out of range.
 * This is how an auditor recovers what the node witnessed, for offline
 * reconciliation against the SQLite log. */
bool supervisor_retained(const SupervisorState *s, uint8_t index,
                         uint64_t *audit_ref, uint8_t *digest_out);

#endif /* GOVERNED_EDGE_AI_SUPERVISOR_STATE_H */
