# Operator Dashboard

Browser-based dashboard for the governed edge AI demonstrator. Served over the local network (Wi-Fi 6) by the audit service backend. No outbound connections.

## Purpose

Provides the operator with a live view of:

- Recent inference events (detection label, confidence score, command issued)
- Flagged events (low confidence, potential drift, transmit failures)
- Kill-switch and system status
- Events suppressed by the oversight node, with the reason recorded against each
- Session history

The operator-facing governance state is not here. It is on the UNO R4 WiFi's 12x8 LED matrix, driven from that board's own state machine. A dashboard served by the governance host cannot be trusted to report that the governance host has stopped.

## Access model

Local network only. The service binds to the board's LAN interface and is not reachable from the public internet by default. No authentication is implemented in the initial version; network-level access control (operator's own router) is the boundary.

## Status

Backend implemented in `audit-service/dashboard/` (FastAPI, read-only, 100% test coverage). This directory holds the browser front end, which is not yet written.
