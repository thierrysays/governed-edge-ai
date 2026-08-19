# Audit Service

SQLite-based append-only audit log and local web service for the governed edge AI demonstrator.

## Schema

See `schema.sql`. Every inference-to-actuation pair is recorded: timestamp, detection type and label, confidence score, command issued, STM32 acknowledgement, and who initiated it.

Three actors are recognised:

| Actor | Means |
|---|---|
| `ai` | Inference-initiated, from the perception pipeline |
| `human_override` | Operator action, including the oversight node's physical button |
| `oversight` | Machine-initiated by the UNO R4 WiFi: governance heartbeat lost, attestation mismatch |

The logging service enforces append-only semantics. No DELETE is issued by any component, and the only UPDATEs are `stm32_ack` (write-once) and `flag` (one-way, 0 to 1).

`fetch_event()` is the sole read path on the write side. It exists so the oversight tier can hash the row exactly as stored rather than hashing the caller's idea of what it wrote.

## Governance rationale

ISO 42001 §9.1 requires records of AI system performance and decision outcomes. The append-only constraint prevents retroactive alteration of the decision record through the service. Physical separation of the audit SSD from the OS volume means a system compromise does not trivially erase the log.

Append-only is a property of the writer, not of the bytes. Anyone who can reach the file can rewrite it, and nothing in the file would show that a row had changed. That gap is closed outside this service: `linux-stack/oversight/attestation.py` folds each stored row into a SHA-256 hash chain and publishes the head to the UNO R4 WiFi, which retains it off this host. Reconciling a recomputed chain against those digests detects edits, deletions, backdating and reordering.

## Dashboard

A lightweight Python web service exposes the audit log to a browser dashboard over the local network (Wi-Fi 6). No outbound telemetry. No cloud relay.

## Planned: LLM query interface

A local LLM running on the NPU side would allow natural-language queries over the audit log: "how many commands were suppressed in the last hour?", "show all events where confidence was below 0.6". Read-only access. Not implemented.

## Structure

```
audit-service/
  schema.sql          SQLite schema
  logger.py           Append-only write service, plus fetch_event() for attestation
  dashboard/          FastAPI read-only dashboard
  tests/              99 tests, 100% line coverage
  requirements.txt
```

## Status

Implemented and tested. `make audit-test` runs the suite.

**Migration note.** The `oversight` actor and detection type were added with the fourth board. `schema.sql` uses `CREATE TABLE IF NOT EXISTS`, so a database created before that change keeps the old CHECK constraints, and SQLite cannot alter a CHECK in place. Rebuild the table to migrate; in the demonstrator a session is one power cycle, so a new session starts a new file.
