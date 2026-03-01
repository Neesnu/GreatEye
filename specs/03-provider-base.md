# Provider Base Class

## Overview
Every service integration in Great Eye is a provider. Providers are Python
classes that inherit from BaseProvider and implement a standard interface.
The provider system is designed so that adding a new integration requires
only creating a new file in `src/providers/` — no changes to core code,
no manual registration, no schema migrations.

This document defines the BaseProvider contract, the registration mechanism,
the permission registration pattern, and the rules every provider must follow.

## BaseProvider Abstract Class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
from datetime import datetime


class HealthStatus(Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


@dataclass
class HealthResult:
    status: HealthStatus
    message: str
    response_time_ms: Optional[float] = None  # How long the check took
    details: Optional[dict] = None            # Provider-specific diagnostic info


@dataclass
class SummaryResult:
    data: dict          # Provider-specific key metrics
    fetched_at: datetime


@dataclass
class DetailResult:
    data: dict          # Provider-specific detailed data
    fetched_at: datetime


@dataclass
class ActionDefinition:
    key: str                    # e.g., "search", "pause", "restart"
    display_name: str           # e.g., "Search for Episodes"
    permission: str             # e.g., "sonarr.search"
    category: str               # "action" or "admin"
    confirm: bool = False       # Require user confirmation before executing
    confirm_message: str = ""   # Message shown in confirmation dialog
    params_schema: dict = None  # JSON schema for action parameters (if any)


@dataclass
class ActionResult:
    success: bool
    message: str
    data: Optional[dict] = None     # Any return data
    invalidate_cache: bool = True   # Whether to refresh cached data after


@dataclass
class ProviderMeta:
    type_id: str                # Unique identifier, e.g., "sonarr"
    display_name: str           # Human-readable, e.g., "Sonarr"
    icon: str                   # Icon class or filename
    category: str               # "download_client", "media", "dns",
                                # "playback", "requests", "runtime"
    config_schema: dict         # Required configuration fields
    default_intervals: dict     # Default polling intervals
    permissions: list           # Permission definitions to register


class BaseProvider(ABC):
    """
    Abstract base class for all Great Eye providers.

    Every provider must implement the abstract methods defined here.
    The registry handles instantiation, passing the decrypted config
    and instance metadata.
    """

    def __init__(self, instance_id: int, display_name: str, config: dict):
        """
        Called by the registry with decrypted configuration.

        Args:
            instance_id: Database ID of this provider instance
            display_name: User-assigned name (e.g., "Sonarr (4K)")
            config: Decrypted configuration dict matching config_schema
        """
        self.instance_id = instance_id
        self.display_name = display_name
        self.config = config

    @staticmethod
    @abstractmethod
    def meta() -> ProviderMeta:
        """
        Return provider type metadata. Called during auto-discovery
        to register the provider type. This is a static method because
        it describes the type, not an instance.
        """
        ...

    @abstractmethod
    async def health_check(self) -> HealthResult:
        """
        Check if the upstream service is reachable and healthy.

        Must complete within 5 seconds. Should test actual connectivity
        (not just that a URL is configured). Prefer hitting a lightweight
        endpoint like /api/v1/system/status rather than a data-heavy one.

        Returns:
            HealthResult with status, message, and optional diagnostics.

        Must not raise exceptions — all errors caught and returned as
        HealthResult with DOWN or DEGRADED status.
        """
        ...

    @abstractmethod
    async def get_summary(self) -> SummaryResult:
        """
        Fetch key metrics for the dashboard summary card.

        Must complete within 10 seconds. Should return the minimum data
        needed to render a useful card: counts, rates, key status info.
        Heavy data belongs in get_detail().

        The data dict structure is provider-specific but should be
        consistent across calls (same keys every time) so the template
        can rely on them.

        Returns:
            SummaryResult with data dict and fetch timestamp.

        Must not raise exceptions — return empty/partial data on error.
        """
        ...

    @abstractmethod
    async def get_detail(self) -> DetailResult:
        """
        Fetch detailed data for the drill-down view.

        Must complete within 15 seconds. This is where lists, tables,
        and comprehensive data live. Called on-demand when a user opens
        the detail view, cached briefly.

        Returns:
            DetailResult with data dict and fetch timestamp.

        Must not raise exceptions — return empty/partial data on error.
        """
        ...

    @abstractmethod
    def get_actions(self) -> list[ActionDefinition]:
        """
        Return the list of actions this provider supports.

        Called during registration and on-demand for UI rendering.
        This is not async because it returns static definitions,
        not live data.

        Returns:
            List of ActionDefinition describing each available action.
        """
        ...

    @abstractmethod
    async def execute_action(self, action: str, params: dict) -> ActionResult:
        """
        Execute a user-triggered action.

        Must complete within 30 seconds. Permission checking is handled
        by the registry/route layer before this is called — the provider
        can assume the user is authorized.

        Args:
            action: The action key (e.g., "search", "pause")
            params: Action parameters matching the action's params_schema

        Returns:
            ActionResult with success status and message.

        Must not raise exceptions — return ActionResult(success=False)
        on error.
        """
        ...

    async def cleanup(self):
        """
        Optional. Called when a provider instance is disabled or removed.
        Use for closing persistent connections, canceling timers, etc.
        Default implementation does nothing.
        """
        pass

    async def validate_config(self) -> tuple[bool, str]:
        """
        Optional. Validate the provider configuration beyond schema checks.
        Called during setup wizard and when admin saves config changes.
        Can test connectivity, verify API key validity, etc.

        Returns:
            (is_valid, message) tuple.

        Default implementation returns (True, "OK").
        """
        return True, "OK"
```

## Provider Registration

### Auto-Discovery
On startup, the registry scans `src/providers/` for Python modules. For
each module, it looks for classes that inherit from BaseProvider and calls
their `meta()` static method to collect type metadata.

```python
# registry.py (simplified discovery logic)
import importlib
import pkgutil
from pathlib import Path

def discover_providers():
    """Scan providers directory, import modules, find BaseProvider subclasses."""
    providers_path = Path(__file__).parent
    discovered = {}

    for importer, module_name, is_pkg in pkgutil.iter_modules([str(providers_path)]):
        if module_name in ("base", "registry"):
            continue
        module = importlib.import_module(f"src.providers.{module_name}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type)
                and issubclass(attr, BaseProvider)
                and attr is not BaseProvider):
                meta = attr.meta()
                discovered[meta.type_id] = attr

    return discovered
```

### Registration Flow
```
App startup
  → discover_providers() scans src/providers/
  → For each discovered provider class:
      1. Call ProviderClass.meta() to get metadata
      2. Upsert provider_types table with metadata
      3. Register permissions from meta.permissions:
         - Upsert permissions table
         - Auto-assign to admin role
         - Auto-assign read/action to user/viewer roles by category
  → For each enabled provider_instance in database:
      1. Load config, decrypt secrets
      2. Instantiate provider class with config
      3. Run initial health check
      4. Schedule polling jobs
```

### Permission Registration
Each provider defines its permissions in the meta() method. Permissions
follow the format `{type_id}.{action}`.

Example for a Sonarr provider:
```python
permissions=[
    PermissionDef(
        key="sonarr.view",
        display_name="View Sonarr Data",
        description="View library, queue, and status information",
        category="read"
    ),
    PermissionDef(
        key="sonarr.search",
        display_name="Search for Episodes",
        description="Trigger automatic or interactive search",
        category="action"
    ),
    PermissionDef(
        key="sonarr.import",
        display_name="Import Episodes",
        description="Manually import downloaded files",
        category="action"
    ),
    PermissionDef(
        key="sonarr.delete",
        display_name="Delete Series",
        description="Remove series and optionally delete files",
        category="admin"
    ),
]
```

The PermissionDef dataclass:
```python
@dataclass
class PermissionDef:
    key: str            # Unique permission key
    display_name: str   # Human-readable name
    description: str    # Explanation for admin UI
    category: str       # "read", "action", or "admin"
```

## Config Schema Definition
The config_schema in ProviderMeta defines what configuration fields the
admin must provide when creating an instance. This drives the admin UI
form rendering.

### Field Types
| Type     | UI Element    | Storage                | Notes                      |
|----------|---------------|------------------------|----------------------------|
| url      | URL input     | Plaintext in JSON      | Validated as http/https    |
| string   | Text input    | Plaintext in JSON      |                            |
| secret   | Password input| Fernet encrypted       | Masked in UI (last 4 chars)|
| integer  | Number input  | Plaintext in JSON      | With optional min/max      |
| boolean  | Toggle        | Plaintext in JSON      |                            |
| select   | Dropdown      | Plaintext in JSON      | Options defined in schema  |

### Schema Example (qBittorrent)
```json
{
  "fields": [
    {
      "key": "url",
      "label": "qBittorrent URL",
      "type": "url",
      "required": true,
      "placeholder": "http://10.0.0.45:8080",
      "help_text": "Full URL to qBittorrent Web UI"
    },
    {
      "key": "username",
      "label": "Username",
      "type": "string",
      "required": false,
      "help_text": "Leave blank if authentication is disabled"
    },
    {
      "key": "password",
      "label": "Password",
      "type": "secret",
      "required": false
    },
    {
      "key": "recent_limit",
      "label": "Recent Transfers Limit",
      "type": "integer",
      "required": false,
      "default": 30,
      "min": 10,
      "max": 100,
      "help_text": "Number of recent transfers to display"
    }
  ]
}
```

### Schema Example (Pi-hole)
```json
{
  "fields": [
    {
      "key": "url",
      "label": "Pi-hole URL",
      "type": "url",
      "required": true,
      "placeholder": "http://10.0.0.53",
      "help_text": "URL to Pi-hole admin interface (without /admin)"
    },
    {
      "key": "api_token",
      "label": "API Token",
      "type": "secret",
      "required": true,
      "help_text": "Found in Pi-hole Settings → API → Show API Token"
    }
  ]
}
```

### Schema Example (Docker)
```json
{
  "fields": [
    {
      "key": "socket_path",
      "label": "Docker Socket Path",
      "type": "string",
      "required": true,
      "default": "/var/run/docker.sock",
      "help_text": "Path to Docker socket (local) or tcp://host:port (remote)"
    },
    {
      "key": "exclude_containers",
      "label": "Excluded Container Names",
      "type": "string",
      "required": false,
      "help_text": "Comma-separated list of container names to hide"
    }
  ]
}
```

## Provider Implementation Rules

### Must Follow
1. **Never raise exceptions** from any abstract method. Catch all errors
   internally and return appropriate result objects with error information.

2. **Respect timeouts.** Use `asyncio.wait_for()` or `httpx` timeout
   parameters. Health: 5s, summary: 10s, detail: 15s, actions: 30s.

3. **Use httpx for HTTP calls.** All providers use `httpx.AsyncClient` for
   upstream API communication. The base class provides a preconfigured
   client via `self.http_client` (set up by the registry on instantiation).

4. **Return consistent data shapes.** The data dict from get_summary() and
   get_detail() should always contain the same keys. Use None or empty
   values for missing data, not missing keys. Templates depend on this.

5. **Register all permissions.** Every action the provider can perform must
   have a corresponding permission in meta().permissions. The route layer
   checks permissions before calling execute_action().

6. **Encrypt nothing yourself.** The registry handles config decryption
   before passing it to __init__. The provider never touches Fernet directly.

7. **Log with context.** Use structured logging with instance_id and
   provider type so log messages are traceable:
   ```python
   logger.warning("API returned 503", extra={
       "provider": self.meta().type_id,
       "instance_id": self.instance_id,
       "instance_name": self.display_name
   })
   ```

8. **Degrade gracefully on partial data.** If an upstream API returns some
   data but errors on part of the request, return what you have. A summary
   card showing "12 series, queue unavailable" is better than no card.

### Should Follow
1. **Prefer lightweight endpoints for health checks.** Use system/status
   or ping endpoints rather than fetching full data sets.

2. **Minimize API calls per poll.** Batch where the upstream API supports
   it. One call returning 5 fields is better than 5 calls for 1 field each.

3. **Include response time in health checks.** Measuring API latency helps
   detect degradation before full failure.

4. **Document upstream API version compatibility.** Note which API versions
   the provider supports and what happens if the upstream is an older version.

5. **Provide validate_config().** Testing connectivity during setup saves
   the user from wondering why a provider shows "down" after configuration.

## HTTP Client Configuration
The registry provides each provider instance with a preconfigured httpx
AsyncClient. Providers access it via `self.http_client`.

```python
# Registry creates this for each instance
client = httpx.AsyncClient(
    base_url=config["url"],
    timeout=httpx.Timeout(
        connect=5.0,
        read=10.0,
        write=5.0,
        pool=5.0
    ),
    headers={
        "User-Agent": "GreatEye/1.0"
    },
    # Provider-specific auth headers added per type
)
```

Providers add their own auth headers in __init__ or per-request:
```python
# API key in header (Sonarr/Radarr pattern)
self.http_client.headers["X-Api-Key"] = config["api_key"]

# Or per-request for token-based auth
response = await self.http_client.get("/api/endpoint", headers={
    "Authorization": f"Bearer {self.config['token']}"
})
```

## Example Provider Implementation

A minimal but complete provider to illustrate the pattern:

```python
# src/providers/example.py
import httpx
from datetime import datetime
from providers.base import (
    BaseProvider, ProviderMeta, PermissionDef,
    HealthResult, HealthStatus, SummaryResult, DetailResult,
    ActionDefinition, ActionResult
)


class ExampleProvider(BaseProvider):
    """Example provider demonstrating the full interface."""

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="example",
            display_name="Example Service",
            icon="example-icon",
            category="media",
            config_schema={
                "fields": [
                    {"key": "url", "label": "URL", "type": "url", "required": True},
                    {"key": "api_key", "label": "API Key", "type": "secret", "required": True},
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 60,
                "detail_cache_seconds": 300
            },
            permissions=[
                PermissionDef("example.view", "View Data", "View example data", "read"),
                PermissionDef("example.refresh", "Refresh", "Trigger a refresh", "action"),
            ]
        )

    async def health_check(self) -> HealthResult:
        try:
            start = datetime.utcnow()
            response = await self.http_client.get("/api/v1/system/status")
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if response.status_code == 200:
                return HealthResult(
                    status=HealthStatus.UP,
                    message="Connected",
                    response_time_ms=elapsed
                )
            else:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Unexpected status: {response.status_code}",
                    response_time_ms=elapsed
                )
        except httpx.TimeoutException:
            return HealthResult(
                status=HealthStatus.DOWN,
                message="Connection timed out"
            )
        except httpx.ConnectError:
            return HealthResult(
                status=HealthStatus.DOWN,
                message="Connection refused"
            )
        except Exception as e:
            return HealthResult(
                status=HealthStatus.DOWN,
                message=f"Unexpected error: {str(e)}"
            )

    async def get_summary(self) -> SummaryResult:
        try:
            response = await self.http_client.get("/api/v1/summary")
            data = response.json()
            return SummaryResult(
                data={
                    "total_items": data.get("total", 0),
                    "active_items": data.get("active", 0),
                    "error_count": data.get("errors", 0),
                },
                fetched_at=datetime.utcnow()
            )
        except Exception:
            return SummaryResult(
                data={
                    "total_items": None,
                    "active_items": None,
                    "error_count": None,
                },
                fetched_at=datetime.utcnow()
            )

    async def get_detail(self) -> DetailResult:
        try:
            response = await self.http_client.get("/api/v1/items")
            return DetailResult(
                data={"items": response.json()},
                fetched_at=datetime.utcnow()
            )
        except Exception:
            return DetailResult(
                data={"items": []},
                fetched_at=datetime.utcnow()
            )

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="refresh",
                display_name="Refresh Library",
                permission="example.refresh",
                category="action",
                confirm=False
            ),
        ]

    async def execute_action(self, action: str, params: dict) -> ActionResult:
        if action == "refresh":
            try:
                response = await self.http_client.post("/api/v1/refresh")
                if response.status_code == 200:
                    return ActionResult(
                        success=True,
                        message="Library refresh triggered"
                    )
                return ActionResult(
                    success=False,
                    message=f"Refresh failed: {response.status_code}"
                )
            except Exception as e:
                return ActionResult(
                    success=False,
                    message=f"Refresh failed: {str(e)}"
                )

        return ActionResult(success=False, message=f"Unknown action: {action}")

    async def validate_config(self) -> tuple[bool, str]:
        try:
            response = await self.http_client.get("/api/v1/system/status")
            if response.status_code == 200:
                return True, "Connection successful"
            elif response.status_code == 401:
                return False, "Invalid API key"
            else:
                return False, f"Unexpected response: {response.status_code}"
        except httpx.ConnectError:
            return False, f"Cannot connect to {self.config['url']}"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
```

## Provider Registry Interface

The registry is the central coordinator. Providers never interact with
the database, scheduler, or cache directly — the registry mediates.

```python
class ProviderRegistry:
    """Manages provider type discovery and instance lifecycle."""

    async def discover_and_register(self) -> dict[str, type]:
        """Scan for providers, register types and permissions."""

    async def initialize_instances(self):
        """Load enabled instances from DB, instantiate, schedule polling."""

    async def create_instance(self, type_id: str, display_name: str,
                              config: dict) -> int:
        """Create a new provider instance. Returns instance_id."""

    async def update_instance(self, instance_id: int, **kwargs):
        """Update instance config, display name, intervals, etc."""

    async def remove_instance(self, instance_id: int):
        """Disable and remove a provider instance."""

    async def get_instance(self, instance_id: int) -> BaseProvider:
        """Get a live provider instance by ID."""

    async def get_all_instances(self) -> list[BaseProvider]:
        """Get all enabled provider instances."""

    async def get_instances_by_type(self, type_id: str) -> list[BaseProvider]:
        """Get all enabled instances of a specific type."""

    async def get_health(self, instance_id: int) -> HealthResult:
        """Get cached health for an instance."""

    async def get_summary(self, instance_id: int) -> SummaryResult:
        """Get cached summary for an instance."""

    async def get_detail(self, instance_id: int) -> DetailResult:
        """Get detail data (from cache or fresh fetch)."""

    async def execute_action(self, instance_id: int, action: str,
                             params: dict, user_id: int) -> ActionResult:
        """Execute an action, log it, invalidate cache if needed."""

    async def get_dashboard_state(self) -> dict:
        """Get all health + summary data for all instances (batch endpoint)."""
```

The registry also manages the SSE event bus — when cache is updated after
a poll, the registry publishes an event that SSE connections pick up.
