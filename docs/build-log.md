# Build Log

Running record of decisions, discoveries, and blockers for the Glossolalie Advisory case study and the Réseau Daubigny presentation.

---

## 2026-08-05 — Repository initialised

**Context.** Board obtained via Arduino 21st-anniversary VENTUNO Q giveaway (contest entry submitted August 2026). Availability listed as Q2 2026; hardware not yet in hand at time of writing.

**Decisions taken today.**

- Repository name: `governed-edge-ai`. Hardware-agnostic name survives board naming changes; `ventuno-q-gov` was the runner-up.
- Licence: Apache 2.0 for code (explicit patent non-assertion clause, appropriate for enterprise-adjacent open source); CERN OHL-P v2 to be added when hardware design files are committed; CC BY 4.0 for documentation.
- Initial folder structure follows the layout from the project brief: `linux-stack/`, `rt-control/`, `audit-service/`, `dashboard/`, `docs/`.
- Audit log schema drafted in `audit-service/schema.sql`. Append-only semantics enforced at the service layer; schema includes a session registry table for per-power-cycle traceability.

**Open items blocking progress.**

- Official VENTUNO Q pinout not yet published; GPIO and MIPI-CSI connector assumptions are provisional throughout the codebase.
- Robotic arm model TBD; order deferred until budget vs degrees-of-freedom trade-off is resolved.
- Board power draw under sustained NPU load unknown; PSU wattage not finalised.

**Next steps.**

1. Monitor Arduino forums and official channels for pinout publication.
2. Draft IPC protocol specification between Linux and STM32H5 (see open governance questions in `docs/governance-mapping.md`).
3. Begin Python skeleton for the audit logger once pinout is confirmed.
4. Open GitHub Discussions or Issues for community input on arm model selection.
