/*
 * ipc_frame.h - IPC frame codec, oversight subset.
 *
 * Wire format is identical to linux-stack/ipc/codec.py and
 * alvik-firmware/ipc_codec.py:
 *
 *   [0]     magic 0xA5
 *   [1]     message type
 *   [2..3]  payload length, uint16 LE
 *   [4..]   payload
 *   [..]    CRC-16/CCITT over bytes 0..3+N, uint16 LE
 *
 * Only the five oversight message types are implemented here. The UNO R4
 * WiFi never sees a CommandRequest: it is not on the actuation path, and
 * leaving those decoders out is a deliberate reduction of what this board
 * can be talked into doing.
 *
 * Governed Edge AI - Glossolalie Advisory. Apache 2.0.
 */

#ifndef GOVERNED_EDGE_AI_IPC_FRAME_H
#define GOVERNED_EDGE_AI_IPC_FRAME_H

#include <stdint.h>
#include <stddef.h>

#define IPC_MAGIC 0xA5
#define IPC_DIGEST_BYTES 32
#define IPC_MAX_FRAME 64  /* largest oversight frame is ATTEST_DIGEST at 46 */

/* Message types: VENTUNO Q -> R4 */
#define MSG_SUPERVISOR_HEARTBEAT 0x30
#define MSG_ATTEST_DIGEST        0x31
/* Message types: R4 -> VENTUNO Q */
#define MSG_OVERRIDE_ASSERT      0xA0
#define MSG_OVERRIDE_CLEAR       0xA1
#define MSG_ATTEST_ACK           0xA2

/* OverrideReason */
#define OVR_OPERATOR_BUTTON           0x01
#define OVR_GOVERNANCE_HEARTBEAT_LOST 0x02
#define OVR_ATTESTATION_MISMATCH      0x03
#define OVR_REMOTE_CONSOLE            0x04
#define OVR_LATCH_MISMATCH            0x05

/* AttestVerdict */
#define ATT_CHAIN_OK    0x00
#define ATT_CHAIN_BREAK 0x01
#define ATT_GAP         0x02

/* SystemState, as reported by the governance tier */
#define SYS_ARMED  0x00
#define SYS_HALTED 0x01
#define SYS_BUSY   0x02
#define SYS_FAULT  0x03

typedef struct {
  uint64_t last_audit_ref;
  uint8_t  system_state;
  uint32_t events_logged;
  uint32_t commands_sent;
} SupervisorHeartbeat;

typedef struct {
  uint64_t audit_ref;
  uint8_t  digest[IPC_DIGEST_BYTES];
} AttestDigest;

uint16_t ipc_crc16_ccitt(const uint8_t *data, size_t len);

/* Encoders. Each returns the number of bytes written into out. */
size_t ipc_encode_override_assert(uint8_t *out, uint64_t timestamp_us, uint8_t reason);
size_t ipc_encode_override_clear(uint8_t *out, uint64_t timestamp_us);
size_t ipc_encode_attest_ack(uint8_t *out, uint64_t audit_ref, uint8_t verdict);

/*
 * Incremental parser for the inbound stream. Feed bytes one at a time; the
 * callbacks fire once a complete, CRC-valid frame of a known type arrives.
 * Bytes before a magic byte are discarded, matching the receiver behaviour
 * in docs/ipc-protocol.md.
 */
typedef struct {
  uint8_t buf[IPC_MAX_FRAME];
  size_t  len;
  size_t  expected;   /* 0 until the header is complete */
} IpcParser;

void ipc_parser_reset(IpcParser *p);

/*
 * Returns one of MSG_SUPERVISOR_HEARTBEAT / MSG_ATTEST_DIGEST when a frame
 * of that type completed on this byte, or 0 otherwise. On a hit, the decoded
 * value is written to the matching out-parameter.
 */
uint8_t ipc_parser_feed(IpcParser *p, uint8_t byte,
                        SupervisorHeartbeat *hb, AttestDigest *digest);

#endif /* GOVERNED_EDGE_AI_IPC_FRAME_H */
