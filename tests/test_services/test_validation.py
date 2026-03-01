"""Tests for SSRF and action parameter validation."""
import pytest

from src.utils.validation import validate_action_params, validate_provider_url


class TestSSRFValidation:
    def test_lan_allowed(self):
        ok, _ = validate_provider_url("http://10.0.0.45:8080")
        assert ok

    def test_private_network_allowed(self):
        ok, _ = validate_provider_url("http://192.168.1.100:8989")
        assert ok

    def test_hostname_allowed(self):
        ok, _ = validate_provider_url("http://sonarr.local:8989")
        assert ok

    def test_aws_metadata_blocked(self):
        ok, msg = validate_provider_url("http://169.254.169.254/latest/meta-data/")
        assert not ok
        assert "Blocked" in msg

    def test_gcp_metadata_blocked(self):
        ok, _ = validate_provider_url("http://metadata.google.internal")
        assert not ok

    def test_alibaba_metadata_blocked(self):
        ok, _ = validate_provider_url("http://100.100.100.200")
        assert not ok

    def test_loopback_blocked(self):
        ok, msg = validate_provider_url("http://127.0.0.1:8080")
        assert not ok
        assert "Blocked" in msg

    def test_loopback_127_x_blocked(self):
        ok, _ = validate_provider_url("http://127.0.0.2:8080")
        assert not ok

    def test_link_local_blocked(self):
        ok, _ = validate_provider_url("http://169.254.1.1")
        assert not ok

    def test_ftp_scheme_blocked(self):
        ok, _ = validate_provider_url("ftp://10.0.0.1/file")
        assert not ok

    def test_no_scheme_blocked(self):
        ok, _ = validate_provider_url("10.0.0.1:8080")
        assert not ok

    def test_no_hostname(self):
        ok, _ = validate_provider_url("http://")
        assert not ok

    def test_https_allowed(self):
        ok, _ = validate_provider_url("https://sonarr.example.com")
        assert ok


class TestActionParamValidation:
    def test_no_schema_passes(self):
        ok, _ = validate_action_params({"any": "thing"}, None)
        assert ok

    def test_required_present(self):
        schema = {"properties": {"id": {"type": "integer", "required": True}}}
        ok, _ = validate_action_params({"id": "5"}, schema)
        assert ok

    def test_required_missing(self):
        schema = {"properties": {"id": {"type": "integer", "required": True}}}
        ok, msg = validate_action_params({}, schema)
        assert not ok
        assert "required" in msg.lower()

    def test_optional_missing_ok(self):
        schema = {"properties": {"id": {"type": "integer", "required": False}}}
        ok, _ = validate_action_params({}, schema)
        assert ok

    def test_integer_valid(self):
        schema = {"properties": {"n": {"type": "integer", "required": True, "min": 1, "max": 100}}}
        ok, _ = validate_action_params({"n": "50"}, schema)
        assert ok

    def test_integer_not_numeric(self):
        schema = {"properties": {"n": {"type": "integer", "required": True}}}
        ok, _ = validate_action_params({"n": "abc"}, schema)
        assert not ok

    def test_integer_below_min(self):
        schema = {"properties": {"n": {"type": "integer", "required": True, "min": 1}}}
        ok, _ = validate_action_params({"n": "0"}, schema)
        assert not ok

    def test_integer_above_max(self):
        schema = {"properties": {"n": {"type": "integer", "required": True, "max": 10}}}
        ok, _ = validate_action_params({"n": "11"}, schema)
        assert not ok

    def test_string_valid(self):
        schema = {"properties": {"q": {"type": "string", "required": True, "max_length": 100}}}
        ok, _ = validate_action_params({"q": "hello"}, schema)
        assert ok

    def test_string_too_long(self):
        schema = {"properties": {"q": {"type": "string", "required": True, "max_length": 5}}}
        ok, _ = validate_action_params({"q": "toolong"}, schema)
        assert not ok

    def test_hex_string_valid(self):
        schema = {"properties": {"hash": {"type": "hex_string", "required": True}}}
        ok, _ = validate_action_params({"hash": "ab12ef"}, schema)
        assert ok

    def test_hex_string_invalid(self):
        schema = {"properties": {"hash": {"type": "hex_string", "required": True}}}
        ok, _ = validate_action_params({"hash": "xyz"}, schema)
        assert not ok
