"""Pi-hole provider — connects to Pi-hole v6 REST API for DNS blocking statistics."""

import asyncio
from datetime import datetime
from typing import Any

import httpx
import structlog

from src.providers.base import (
    ActionDefinition,
    ActionResult,
    BaseProvider,
    DetailResult,
    HealthResult,
    HealthStatus,
    PermissionDef,
    ProviderMeta,
    SummaryResult,
)

logger = structlog.get_logger()


class PiholeProvider(BaseProvider):
    """Pi-hole v6 DNS blocking provider with session-based auth."""

    _sid: str | None = None

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="pihole",
            display_name="Pi-hole",
            icon="pihole",
            category="network",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "Pi-hole URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.1",
                        "help_text": "URL to Pi-hole web interface (port 80 or 8080)",
                    },
                    {
                        "key": "password",
                        "label": "Web Interface Password",
                        "type": "secret",
                        "required": False,
                        "help_text": "Pi-hole web interface password (leave blank if none set)",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 60,
                "detail_cache_seconds": 300,
            },
            permissions=[
                PermissionDef("pihole.view", "View Pi-hole Data", "Stats, query log, lists", "read"),
                PermissionDef("pihole.blocking", "Toggle Blocking", "Enable/disable DNS blocking", "action"),
                PermissionDef("pihole.gravity", "Update Gravity", "Refresh blocklists", "action"),
            ],
        )

    # ------------------------------------------------------------------
    # Session Auth
    # ------------------------------------------------------------------

    async def _authenticate(self) -> bool:
        """Authenticate to Pi-hole and store session cookie."""
        password = self.config.get("password", "")
        if not password:
            return True  # No password configured, try unauthenticated

        try:
            resp = await self.http_client.post("/api/auth", json={"password": password})
            if resp.status_code == 200:
                data = resp.json()
                session = data.get("session", {})
                if session.get("valid"):
                    self._sid = session.get("sid", "")
                    self.http_client.cookies.set("sid", self._sid)
                    return True
            return False
        except Exception:
            return False

    async def cleanup(self) -> None:
        """Log out of Pi-hole to release the API session slot."""
        if self._sid and self.http_client:
            try:
                await self.http_client.delete("/api/auth")
            except Exception:
                pass
            self._sid = None

    async def _api_get(self, path: str, **params: Any) -> httpx.Response:
        """Make an authenticated GET request, re-authenticating on 401."""
        resp = await self.http_client.get(path, params=params)
        if resp.status_code == 401:
            if await self._authenticate():
                resp = await self.http_client.get(path, params=params)
        return resp

    async def _api_post(self, path: str, json_data: dict | None = None) -> httpx.Response:
        """Make an authenticated POST request, re-authenticating on 401."""
        resp = await self.http_client.post(path, json=json_data)
        if resp.status_code == 401:
            if await self._authenticate():
                resp = await self.http_client.post(path, json=json_data)
        return resp

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        try:
            if self.config.get("password") and not self._sid:
                await self._authenticate()

            start = datetime.utcnow()
            resp = await self._api_get("/api/dns/blocking")
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if resp.status_code == 401:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message="Auth required — set password",
                    response_time_ms=elapsed,
                )
            if resp.status_code != 200:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message=f"Unexpected status: {resp.status_code}",
                    response_time_ms=elapsed,
                )

            try:
                data = resp.json()
            except Exception:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Not a Pi-hole instance (unexpected response)",
                    response_time_ms=elapsed,
                )
            if "blocking" not in data:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Not a Pi-hole instance (missing blocking field)",
                    response_time_ms=elapsed,
                )
            blocking = data.get("blocking", True)

            if elapsed > 3000:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Slow response ({elapsed:.0f}ms)",
                    response_time_ms=elapsed,
                )

            if not blocking:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message="Blocking disabled",
                    response_time_ms=elapsed,
                    details={"blocking": False},
                )

            return HealthResult(
                status=HealthStatus.UP,
                message="Blocking active",
                response_time_ms=elapsed,
                details={"blocking": True},
            )

        except httpx.TimeoutException:
            return HealthResult(status=HealthStatus.DOWN, message="Connection timed out")
        except httpx.ConnectError:
            return HealthResult(status=HealthStatus.DOWN, message="Connection refused")
        except Exception as e:
            return HealthResult(status=HealthStatus.DOWN, message=f"Error: {str(e)}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_summary(self) -> SummaryResult:
        empty = self._empty_summary()
        try:
            if self.config.get("password") and not self._sid:
                await self._authenticate()

            results = await asyncio.gather(
                self._api_get("/api/stats/summary"),
                self._api_get("/api/dns/blocking"),
                return_exceptions=True,
            )

            stats_resp, blocking_resp = results

            # Parse stats
            queries: dict[str, Any] = {
                "total": 0, "blocked": 0, "percent_blocked": 0,
                "unique_domains": 0, "forwarded": 0, "cached": 0,
            }
            clients: dict[str, Any] = {"active": 0, "total": 0}
            gravity: dict[str, Any] = {"domains_being_blocked": 0, "last_update": ""}
            version: dict[str, Any] = {}

            if not isinstance(stats_resp, Exception) and stats_resp.status_code == 200:
                data = stats_resp.json()
                q = data.get("queries", {})
                queries = {
                    "total": q.get("total", 0),
                    "blocked": q.get("blocked", 0),
                    "percent_blocked": q.get("percent_blocked", 0),
                    "unique_domains": q.get("unique_domains", 0),
                    "forwarded": q.get("forwarded", 0),
                    "cached": q.get("cached", 0),
                }
                c = data.get("clients", {})
                clients = {
                    "active": c.get("active", 0),
                    "total": c.get("total", 0),
                }
                g = data.get("gravity", {})
                gravity = {
                    "domains_being_blocked": g.get("domains_being_blocked", 0),
                    "last_update": g.get("last_update", ""),
                }
                version = data.get("version", {})

            # Parse blocking status
            blocking_enabled = True
            if not isinstance(blocking_resp, Exception) and blocking_resp.status_code == 200:
                blocking_enabled = blocking_resp.json().get("blocking", True)

            return SummaryResult(
                data={
                    "queries": queries,
                    "clients": clients,
                    "gravity": gravity,
                    "blocking_enabled": blocking_enabled,
                    "version": version,
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning("pihole_summary_failed", instance_id=self.instance_id, error=str(e))
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "queries": {
                    "total": 0, "blocked": 0, "percent_blocked": 0,
                    "unique_domains": 0, "forwarded": 0, "cached": 0,
                },
                "clients": {"active": 0, "total": 0},
                "gravity": {"domains_being_blocked": 0, "last_update": ""},
                "blocking_enabled": None,
                "version": {},
            },
            fetched_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    async def get_detail(self) -> DetailResult:
        empty = DetailResult(
            data={
                "stats": {}, "top_domains": [], "top_blocked": [],
                "top_clients": [], "upstreams": [], "query_types": {},
                "blocking_enabled": None, "version": {},
            },
            fetched_at=datetime.utcnow(),
        )
        try:
            if self.config.get("password") and not self._sid:
                await self._authenticate()

            results = await asyncio.gather(
                self._api_get("/api/stats/summary"),
                self._api_get("/api/stats/top_domains", count=20),
                self._api_get("/api/stats/top_blocked", count=20),
                self._api_get("/api/stats/top_clients", count=20),
                self._api_get("/api/stats/upstreams"),
                self._api_get("/api/stats/query_types"),
                self._api_get("/api/dns/blocking"),
                self._api_get("/api/info/version"),
                return_exceptions=True,
            )

            (stats_resp, top_domains_resp, top_blocked_resp, top_clients_resp,
             upstreams_resp, query_types_resp, blocking_resp, version_resp) = results

            # Stats
            stats: dict = {}
            if not isinstance(stats_resp, Exception) and stats_resp.status_code == 200:
                stats = stats_resp.json()

            # Top domains
            top_domains: list[dict] = []
            if not isinstance(top_domains_resp, Exception) and top_domains_resp.status_code == 200:
                td = top_domains_resp.json()
                for item in td.get("domains", td if isinstance(td, list) else []):
                    if isinstance(item, dict):
                        top_domains.append(item)

            # Top blocked
            top_blocked: list[dict] = []
            if not isinstance(top_blocked_resp, Exception) and top_blocked_resp.status_code == 200:
                tb = top_blocked_resp.json()
                for item in tb.get("domains", tb if isinstance(tb, list) else []):
                    if isinstance(item, dict):
                        top_blocked.append(item)

            # Top clients
            top_clients: list[dict] = []
            if not isinstance(top_clients_resp, Exception) and top_clients_resp.status_code == 200:
                tc = top_clients_resp.json()
                for item in tc.get("clients", tc if isinstance(tc, list) else []):
                    if isinstance(item, dict):
                        top_clients.append(item)

            # Upstreams
            upstreams: list[dict] = []
            if not isinstance(upstreams_resp, Exception) and upstreams_resp.status_code == 200:
                us = upstreams_resp.json()
                for item in us.get("upstreams", us if isinstance(us, list) else []):
                    if isinstance(item, dict):
                        upstreams.append(item)

            # Query types
            query_types: dict = {}
            if not isinstance(query_types_resp, Exception) and query_types_resp.status_code == 200:
                query_types = query_types_resp.json()

            # Blocking
            blocking_enabled = None
            if not isinstance(blocking_resp, Exception) and blocking_resp.status_code == 200:
                blocking_enabled = blocking_resp.json().get("blocking", None)

            # Version
            version: dict = {}
            if not isinstance(version_resp, Exception) and version_resp.status_code == 200:
                version = version_resp.json()

            return DetailResult(
                data={
                    "stats": stats,
                    "top_domains": top_domains,
                    "top_blocked": top_blocked,
                    "top_clients": top_clients,
                    "upstreams": upstreams,
                    "query_types": query_types,
                    "blocking_enabled": blocking_enabled,
                    "version": version,
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning("pihole_detail_failed", instance_id=self.instance_id, error=str(e))
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="disable_blocking",
                display_name="Disable Blocking",
                permission="pihole.blocking",
                category="action",
                confirm=True,
                confirm_message="Disable DNS blocking? Ads will not be blocked.",
                params_schema={
                    "properties": {"duration": {"type": "integer", "required": False}}
                },
            ),
            ActionDefinition(
                key="enable_blocking",
                display_name="Enable Blocking",
                permission="pihole.blocking",
                category="action",
            ),
            ActionDefinition(
                key="update_gravity",
                display_name="Update Gravity",
                permission="pihole.gravity",
                category="action",
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        try:
            if action == "disable_blocking":
                duration = int(params.get("duration", 300))
                resp = await self._api_post("/api/dns/blocking", json_data={"blocking": False, "timer": duration})
                if resp.status_code == 200:
                    msg = f"Blocking disabled for {duration}s" if duration > 0 else "Blocking disabled indefinitely"
                    return ActionResult(success=True, message=msg, invalidate_cache=True)
                return ActionResult(success=False, message=f"Failed: HTTP {resp.status_code}")

            elif action == "enable_blocking":
                resp = await self._api_post("/api/dns/blocking", json_data={"blocking": True})
                if resp.status_code == 200:
                    return ActionResult(success=True, message="Blocking enabled", invalidate_cache=True)
                return ActionResult(success=False, message=f"Failed: HTTP {resp.status_code}")

            elif action == "update_gravity":
                resp = await self._api_post("/api/action/gravity")
                if resp.status_code == 200:
                    return ActionResult(success=True, message="Gravity update started")
                return ActionResult(success=False, message=f"Failed: HTTP {resp.status_code}")

            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")

        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    # ------------------------------------------------------------------
    # Validate Config
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        try:
            if self.config.get("password"):
                auth_ok = await self._authenticate()
                if not auth_ok:
                    return False, "Invalid password"

            resp = await self._api_get("/api/info/version")
            if resp.status_code == 401:
                return False, "Authentication required — set password"
            if resp.status_code != 200:
                return False, f"Unexpected response: HTTP {resp.status_code}"

            data = resp.json()
            version = data.get("version", {})
            ftl = version.get("ftl", {}).get("version", "unknown") if isinstance(version, dict) else "unknown"
            return True, f"Connected to Pi-hole (FTL v{ftl})"

        except httpx.ConnectError:
            return False, f"Cannot connect to {self.config['url']}"
        except httpx.TimeoutException:
            return False, f"Connection timed out to {self.config['url']}"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
