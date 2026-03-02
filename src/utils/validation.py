import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.packet.net",
    "100.100.100.200",
    "localhost",
    "localhost.localdomain",
}

BLOCKED_NETWORKS = [
    # IPv4
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:127.0.0.0/104"),
]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address falls within any blocked network."""
    for network in BLOCKED_NETWORKS:
        if ip in network:
            return True
    return False


def validate_provider_url(url: str) -> tuple[bool, str]:
    """Validate a provider URL is safe to connect to (SSRF protection per H4)."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return False, "URL must use http or https"

    if not parsed.hostname:
        return False, "URL must include a hostname"

    hostname = parsed.hostname.lower()

    if hostname in BLOCKED_HOSTS:
        return False, f"Blocked host: {hostname}"

    # Check if hostname is a literal IP address
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_blocked_ip(ip):
            return False, f"Blocked network: {hostname}"
        return True, "OK"
    except ValueError:
        pass

    # Hostname is a DNS name — resolve and check the resulting IPs
    try:
        results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        for family, _type, _proto, _canonname, sockaddr in results:
            ip = ipaddress.ip_address(sockaddr[0])
            if _is_blocked_ip(ip):
                return False, f"Hostname {hostname} resolves to blocked address"
    except socket.gaierror:
        pass  # DNS resolution failed — allow (provider health check will fail)

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
