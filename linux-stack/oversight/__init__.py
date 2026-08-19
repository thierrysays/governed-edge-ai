"""
Oversight tier: the Arduino UNO R4 WiFi supervisor node.

This package is the VENTUNO Q side of the oversight link. It holds no
authority to act; its whole purpose is to let a board that sits outside
the perception -> governance -> actuation chain observe that chain and
veto it.

Modules
-------
attestation:
    Rolling SHA-256 hash chain over the audit log, and the offline
    reconciliation that compares the recomputed chain against the digests
    the R4 retained independently of the SQLite file.
supervisor_link:
    SupervisorLink: the VENTUNO Q client for the R4 serial link. Sends
    heartbeats and chain digests, polls for override assertions.
mock_supervisor:
    MockR4Supervisor: pty-based reference model of the R4 firmware state
    machine. Used by the test suite and by `--supervisor mock`, and the
    executable specification the C++ sketch in r4-supervisor/ mirrors.
"""
