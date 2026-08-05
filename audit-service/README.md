# Audit Service

SQLite-based append-only audit log and local web service for the governed edge AI demonstrator.

## Schema

See `schema.sql`. Every inference-to-actuation pair is recorded: timestamp, detection type and label, confidence score, command issued, STM32H5 acknowledgement, and whether the action was AI-initiated or a human override.

The logging service enforces append-only semantics. No UPDATE or DELETE operations are issued by any service component.

## Governance rationale

ISO 42001 §9.1 requires records of AI system performance and decision outcomes. The append-only constraint prevents retroactive alteration of the decision record. Physical separation of the audit SSD from the OS volume means a system compromise does not trivially erase the log.

## Dashboard

A lightweight Python web service exposes the audit log to a browser dashboard over the local network (Wi-Fi 6). No outbound telemetry. No cloud relay.

## LLM query interface

A local LLM running on the NPU side allows natural-language queries over the audit log: for example, "how many commands were suppressed in the last hour?" or "show all events where confidence was below 0.6". The LLM has read-only access to the database.

## Planned structure

```
audit-service/
  schema.sql          SQLite schema
  logger.py           Append-only write service
  dashboard/          Browser dashboard backend (Flask or FastAPI, TBD)
  llm_query/          LLM query endpoint
  requirements.txt
```

## Status

Schema defined. Service implementation pending.
