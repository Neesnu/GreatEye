import ipaddress
from typing import Any
from urllib.parse import urlparse

BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
}

BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
]


def validate_provider_url(url: str) -> tuple[bool, str]:
    """Validate a provider URL is safe to connect to (SSRF protection per H4)."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False, "URL must use http or https"

    if not parsed.hostname:
        return False, "URL must include a hostname"

    hostname = parsed.hostname

    if hostname in BLOCKED_HOSTS:
        return False, f"Blocked host: {hostname}"

    try:
        ip = ipaddress.ip_address(hostname)
        for network in BLOCKED_NETWORKS:
            if ip in network:
                return False, f"Blocked network: {hostname}"
    except ValueError:
        pass

    return True, "OK"


def validate_action_params(
    params: dict[str, Any], schema: dict[str, Any] | None
) -> tuple[bool, str]:
    """Validate action parameters against a JSON schema definition (H5).

    Schema format:
    {
        "properties": {
            "param_name": {"type": "integer", "required": True, "min": 0, "max": 100},
            "query": {"type": "string", "required": True, "max_length": 500},
        }
    }
    """
    if schema is None:
        return True, "OK"

    properties = schema.get("properties", {})

    for name, rules in properties.items():
        value = params.get(name)
        required = rules.get("required", False)

        if value is None:
            if required:
                return False, f"Missing required parameter: {name}"
            continue

        expected_type = rules.get("type", "string")

        if expected_type == "integer":
            try:
                int_val = int(value)
            except (ValueError, TypeError):
                return False, f"Parameter '{name}' must be an integer"
            if "min" in rules and int_val < rules["min"]:
                return False, f"Parameter '{name}' must be >= {rules['min']}"
            if "max" in rules and int_val > rules["max"]:
                return False, f"Parameter '{name}' must be <= {rules['max']}"

        elif expected_type == "string":
            if not isinstance(value, str):
                return False, f"Parameter '{name}' must be a string"
            max_length = rules.get("max_length", 1000)
            if len(value) > max_length:
                return False, f"Parameter '{name}' exceeds max length of {max_length}"

        elif expected_type == "boolean":
            if not isinstance(value, bool) and value not in ("true", "false", "0", "1"):
                return False, f"Parameter '{name}' must be a boolean"

        elif expected_type == "hex_string":
            if not isinstance(value, str):
                return False, f"Parameter '{name}' must be a string"
            try:
                int(value, 16)
            except ValueError:
                return False, f"Parameter '{name}' must be a hex string"

    return True, "OK"
