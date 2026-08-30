"""
Tests for observability.py (optional Sentry error reporting).

init_sentry() must stay a no-op unless SENTRY_DSN is set: that is the
opt-in contract the README's "No outbound telemetry. No cloud relay."
claim depends on.
"""

from unittest.mock import MagicMock, patch

import observability


class TestInitSentry:
    def test_noop_without_dsn(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        with patch("sentry_sdk.init") as mock_init:
            result = observability.init_sentry("test-service")
        assert result is False
        mock_init.assert_not_called()

    def test_noop_with_empty_dsn(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "")
        with patch("sentry_sdk.init") as mock_init:
            result = observability.init_sentry("test-service")
        assert result is False
        mock_init.assert_not_called()

    def test_initializes_when_dsn_set(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://example@o0.ingest.sentry.io/1")
        monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
        monkeypatch.delenv("SENTRY_RELEASE", raising=False)
        monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
        with patch("sentry_sdk.init") as mock_init:
            result = observability.init_sentry("test-service")
        assert result is True
        mock_init.assert_called_once_with(
            dsn="https://example@o0.ingest.sentry.io/1",
            environment="development",
            release=None,
            server_name="test-service",
            send_default_pii=False,
            traces_sample_rate=0.0,
        )

    def test_honours_environment_overrides(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://example@o0.ingest.sentry.io/1")
        monkeypatch.setenv("SENTRY_ENVIRONMENT", "production")
        monkeypatch.setenv("SENTRY_RELEASE", "1.2.3")
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")
        with patch("sentry_sdk.init") as mock_init:
            observability.init_sentry("test-service")
        _, kwargs = mock_init.call_args
        assert kwargs["environment"] == "production"
        assert kwargs["release"] == "1.2.3"
        assert kwargs["traces_sample_rate"] == 0.1
        assert kwargs["send_default_pii"] is False

    def test_send_default_pii_cannot_be_overridden(self, monkeypatch):
        """Audit log contents must never leave the LAN via PII capture,
        regardless of what future env vars might be added around this call."""
        monkeypatch.setenv("SENTRY_DSN", "https://example@o0.ingest.sentry.io/1")
        with patch("sentry_sdk.init") as mock_init:
            observability.init_sentry("test-service")
        assert mock_init.call_args.kwargs["send_default_pii"] is False

    def test_returns_bool_not_sentry_sdk_object(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://example@o0.ingest.sentry.io/1")
        with patch("sentry_sdk.init", MagicMock(return_value="unused")):
            result = observability.init_sentry("test-service")
        assert result is True
