# Security & Operational Hardening

## Overview
Supplemental requirements identified during spec review. These are
low-cost, high-value additions that prevent real issues in a homelab
context — not enterprise ceremony.

## H1: Fernet Key Derivation

**Affects:** spec 02 (data model), any code touching encryption.

The SECRET_KEY environment variable is not used directly as the Fernet
key. It is run through HKDF to produce a proper 32-byte key:

```python
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
import base64

def derive_fernet_key(secret_key: str) -> Fernet:
    """Derive a Fernet encryption key from the SECRET_KEY env var."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"greateye-fernet-v1",   # Static, app-scoped salt
        info=b"provider-config-encryption",
    )
    key = hkdf.derive(secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))
```

The static salt is intentional — we don't need per-value salts for
config encryption, and a static salt means the same SECRET_KEY always
produces the same Fernet key (required for decrypting stored values).

The info parameter scopes the derived key. If we ever need a second
derived key for a different purpose (e.g., session signing), we use
a different info string.

## H2: Docker Socket — Environment Variable Stripping

**Affects:** spec 14 (Docker provider).

Container inspection via the Docker API returns environment variables,
which commonly contain secrets (API keys, database passwords, tokens).
The Docker provider MUST strip environment variables from all data
before it reaches templates or the cache.

**Rule:** The `Env` field from `/containers/{id}/json` is never stored,
cached, or rendered. The provider drops it during normalization:

```python
def _normalize_container(self, raw: dict) -> dict:
    """Normalize container data, stripping sensitive fields."""
    config = raw.get("Config", {})
    return {
        "id": raw["Id"][:12],
        "name": raw["Name"].lstrip("/"),
        "image": config.get("Image"),
        "state": raw.get("State", {}).get("Status"),
        # Env is intentionally omitted
        # Mounts, Ports, etc. are safe to include
    }
```

Additionally, the detail view for Docker containers does NOT expose:
- Environment variables
- Volume mount host paths (could reveal filesystem structure)
- Network configuration details beyond port mappings

The summary and detail views show: container name, image, state,
health, ports, uptime, resource usage. Nothing more.

## H3: SQLite WAL and SSE — No Long-Lived Transactions

**Affects:** spec 01 (architecture), SSE implementation.

The SSE stream reads from an in-memory event bus, NOT directly from
SQLite. The data flow is:

```
Scheduler polls provider
  → writes to provider_cache table (short transaction)
  → publishes event to in-memory event bus
  → event bus fans out to per-connection asyncio.Queues
  → SSE handler reads from Queue, sends to client
```

At no point does the SSE connection hold a database transaction open.
Cache reads (for initial page load or batch polling) are short-lived:
open transaction, read, close. Never hold a transaction across an
await boundary.

**Implementation rule:** All database reads use a context manager
pattern that closes the session/transaction before yielding control:

```python
async def get_cached_summary(instance_id: str) -> dict:
    async with get_db_session() as session:
        result = await session.execute(
            select(ProviderCache).where(
                ProviderCache.instance_id == instance_id,
                ProviderCache.tier == "summary"
            )
        )
        row = result.scalar_one_or_none()
        return json.loads(row.data) if row else None
    # Session is closed here — before any SSE/async work
```

This prevents WAL file growth from blocked checkpoints.

## H4: SSRF Protection on Provider URLs

**Affects:** spec 03 (provider base), validate_config in all providers.

When an admin configures a provider URL, the URL is validated against
a blocklist before any request is made:

```python
import ipaddress
from urllib.parse import urlparse

BLOCKED_HOSTS = {
    "169.254.169.254",        # AWS/cloud metadata
    "metadata.google.internal", # GCP metadata
    "100.100.100.200",        # Alibaba metadata
}

BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local
    ipaddress.ip_network("127.0.0.0/8"),     # Loopback (unless intentional)
]

def validate_provider_url(url: str) -> tuple[bool, str]:
    """Validate a provider URL is safe to connect to."""
    parsed = urlparse(url)

    if not parsed.scheme in ("http", "https"):
        return False, "URL must use http or https"

    if not parsed.hostname:
        return False, "URL must include a hostname"

    hostname = parsed.hostname

    # Check against blocked hosts
    if hostname in BLOCKED_HOSTS:
        return False, f"Blocked host: {hostname}"

    # Check against blocked networks
    try:
        ip = ipaddress.ip_address(hostname)
        for network in BLOCKED_NETWORKS:
            if ip in network:
                return False, f"Blocked network: {hostname}"
    except ValueError:
        pass  # Not an IP address, hostname is fine

    return True, "OK"
```

This runs in `validate_config()` before any connection attempt. The
registry also applies it when creating or updating instances.

Note: Localhost/LAN addresses (10.x, 192.168.x, etc.) are explicitly
ALLOWED — this is a homelab app and most providers run on the local
network. Only cloud metadata and loopback are blocked.

## H5: Action Parameter Validation

**Affects:** spec 03 (provider base), all provider action handlers.

All action parameters from the browser are validated against expected
types before being passed to upstream APIs. No raw user input is
interpolated into URL paths or query strings without validation.

**Rules:**
1. Every ActionDefinition includes a parameter schema (types, required
   fields, valid ranges)
2. The registry validates incoming params against the schema before
   calling execute_action
3. IDs (series_id, episode_id, torrent hash, etc.) are validated as
   the expected type (int, hex string, etc.)
4. String params are length-limited and sanitized
5. No f-string URL construction with user input — use httpx's params
   dict for query parameters and validated path segments

```python
# BAD — user input directly in URL path
url = f"/api/v3/series/{params['series_id']}"

# GOOD — validated integer
series_id = int(params["series_id"])  # Raises ValueError if not int
url = f"/api/v3/series/{series_id}"

# GOOD — query params via httpx
response = await self.http_client.get("/api/v3/queue", params={
    "page": int(params.get("page", 1)),
    "pageSize": min(int(params.get("page_size", 20)), 50),
})
```

## H6: Self-Health Endpoint

**Affects:** spec 17 (API routes).

Great Eye exposes its own health at `GET /health` (no auth required):

```json
{
  "status": "ok",
  "version": "1.0.0",
  "database": "connected",
  "scheduler": "running",
  "providers": {
    "configured": 13,
    "enabled": 13,
    "healthy": 11,
    "degraded": 1,
    "down": 1
  },
  "uptime_seconds": 86400
}
```

This endpoint:
- Requires no authentication (for external monitoring tools)
- Returns 200 if the app is functional, 503 if critically degraded
- Does NOT expose provider names, URLs, or configuration
- Can be used by Unraid's Docker health check, Uptime Kuma, etc.

Add to the Dockerfile HEALTHCHECK:
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8484/health || exit 1
```

## H7: Structured Logging

**Affects:** all provider code, action handlers, auth system.

All log entries include structured context fields. The application
uses Python's standard logging with a JSON formatter in production:

```python
import structlog

logger = structlog.get_logger()

# Provider operations always include instance context
logger.info("summary_fetched",
    provider_type="sonarr",
    instance_id="sonarr-hd",
    duration_ms=145,
    series_count=350
)

# Actions always include user and instance context
logger.info("action_executed",
    provider_type="qbittorrent",
    instance_id="qbit-main",
    action="pause",
    user_id=1,
    username="admin",
    success=True
)

# Auth events include user context
logger.warning("login_failed",
    username="unknown_user",
    ip="10.0.0.100",
    reason="invalid_credentials"
)
```

**Required context by log category:**
| Category          | Required Fields                              |
|-------------------|----------------------------------------------|
| Provider health   | provider_type, instance_id, status, message  |
| Provider data     | provider_type, instance_id, tier, duration_ms|
| Provider action   | provider_type, instance_id, action, user_id  |
| Auth              | username (or user_id), ip, event             |
| System            | component, event                             |

**Log levels:**
- ERROR: Unhandled exceptions, database failures
- WARNING: Provider DOWN, auth failures, rate limit hits
- INFO: Actions executed, provider state changes, logins
- DEBUG: Individual API calls, cache reads/writes, SSE events

Production default: INFO. Configurable via LOG_LEVEL env var.

## H8: Secrets in Error Messages and Logs

**Affects:** all code.

Provider API keys, passwords, tokens, and the SECRET_KEY must never
appear in:
- Log messages (even at DEBUG level)
- Error responses to the browser
- Toast notifications
- Health check details
- The provider_action_log table (params field is sanitized)

**Implementation:** The logging configuration includes a filter that
redacts known secret patterns (API key formats, bearer tokens, etc.)
before they reach the log output. Provider configs are logged with
secret fields replaced by `"***"`.
