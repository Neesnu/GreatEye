"""Tests for structured logging configuration and secret redaction."""

import logging

import pytest

from src.utils.logging import _redact_secrets


class TestSecretRedaction:
    """Test the _redact_secrets structlog processor."""

    def _run(self, event_dict: dict) -> dict:
        """Run the redaction processor on an event dict."""
        return _redact_secrets(None, "info", dict(event_dict))

    def test_redacts_api_key(self):
        result = self._run({"event": "test", "api_key": "sk-12345"})
        assert result["api_key"] == "***"

    def test_redacts_password(self):
        result = self._run({"event": "test", "password": "hunter2"})
        assert result["password"] == "***"

    def test_redacts_secret(self):
        result = self._run({"event": "test", "secret": "mysecret"})
        assert result["secret"] == "***"

    def test_redacts_token(self):
        result = self._run({"event": "test", "token": "abc123"})
        assert result["token"] == "***"

    def test_redacts_secret_key(self):
        result = self._run({"event": "test", "secret_key": "fernet-key"})
        assert result["secret_key"] == "***"

    def test_redacts_authorization(self):
        result = self._run({"event": "test", "authorization": "Bearer xyz"})
        assert result["authorization"] == "***"

    def test_redacts_access_token(self):
        result = self._run({"event": "test", "access_token": "tok123"})
        assert result["access_token"] == "***"

    def test_preserves_safe_fields(self):
        result = self._run({
            "event": "test",
            "username": "admin",
            "instance_id": 42,
            "status": "ok",
        })
        assert result["username"] == "admin"
        assert result["instance_id"] == 42
        assert result["status"] == "ok"

    def test_preserves_event_field(self):
        result = self._run({"event": "action_executed"})
        assert result["event"] == "action_executed"

    def test_redacts_mixed(self):
        result = self._run({
            "event": "test",
            "api_key": "secret123",
            "username": "admin",
            "password": "pass",
        })
        assert result["api_key"] == "***"
        assert result["password"] == "***"
        assert result["username"] == "admin"
