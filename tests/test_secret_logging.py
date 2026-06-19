from __future__ import annotations

import logging

import youtube_core


class _FakeCredentials:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.valid = True


def test_youtube_env_credential_logging_does_not_include_secret_prefixes(monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id-secret-value")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "refresh-token-value")
    monkeypatch.setattr(youtube_core, "Credentials", _FakeCredentials)

    with caplog.at_level(logging.INFO, logger=youtube_core.logger.name):
        youtube_core.get_credentials_from_env()

    log_text = caplog.text
    assert "GOOGLE_CLIENT_ID: PRESENT" in log_text
    assert "GOOGLE_CLIENT_SECRET: PRESENT" in log_text
    assert "GOOGLE_REFRESH_TOKEN: PRESENT" in log_text
    assert "client-id" not in log_text
    assert "client-secret" not in log_text
    assert "refresh-token" not in log_text
