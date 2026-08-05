# Operator Dashboard

Browser-based dashboard for the governed edge AI demonstrator. Served over the local network (Wi-Fi 6) by the audit service backend. No outbound connections.

## Purpose

Provides the operator with a live view of:

- Recent inference events (detection label, confidence score, command issued)
- Flagged events (low confidence, potential drift)
- Kill-switch and system status
- Session history

## Access model

Local network only. The service binds to the board's LAN interface and is not reachable from the public internet by default. No authentication is implemented in the initial version; network-level access control (operator's own router) is the boundary.

## Status

Implementation pending. Backend framework (Flask or FastAPI) TBD once the audit service logger is in place.
