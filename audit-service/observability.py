"""
Optional Sentry error reporting for the audit dashboard.

Off by default. init_sentry() is a no-op unless SENTRY_DSN is set in the
environment, so the "No outbound telemetry. No cloud relay." claim in
README.md keeps holding for anyone who does not opt in. An operator who
wants error reporting sets SENTRY_DSN (and optionally SENTRY_ENVIRONMENT,
SENTRY_RELEASE, SENTRY_TRACES_SAMPLE_RATE) before starting the service.

send_default_pii is forced off regardless of SDK defaults: this service
handles governance audit data, and nothing about a caller beyond the
exception itself should leave the LAN.
"""

import os


def init_sentry(service_name: str) -> bool:
    """Initialize the Sentry SDK if SENTRY_DSN is configured. Returns True
    if it did."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        release=os.environ.get("SENTRY_RELEASE"),
        server_name=service_name,
        send_default_pii=False,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
    )
    return True
