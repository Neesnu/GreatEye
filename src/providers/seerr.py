"""Seerr/Overseerr provider — connects to Seerr API v1 for request management."""

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

# Request status mapping
REQUEST_STATUS_MAP = {
    1: "Pending",
    2: "Approved",
    3: "Declined",
}

# Media status mapping
MEDIA_STATUS_MAP = {
    1: "Unknown",
    2: "Pending",
    3: "Processing",
    4: "Partial",
    5: "Available",
}


class SeerrProvider(BaseProvider):
    """Seerr/Overseerr media request management provider."""

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="seerr",
            display_name="Seerr",
            icon="seerr",
            category="media",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "Seerr URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.45:5055",
                        "help_text": "Full URL to Seerr (or Overseerr) web UI",
                    },
                    {
                        "key": "api_key",
                        "label": "API Key",
                        "type": "secret",
                        "required": True,
                        "help_text": "Found in Settings → General, or set via API_KEY env var",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 60,
                "summary_seconds": 120,
                "detail_cache_seconds": 300,
            },
            permissions=[
                PermissionDef("seerr.view", "View Seerr Data", "Requests, stats, integration status", "read"),
                PermissionDef("seerr.approve", "Approve / Decline Requests", "Approve or decline pending requests", "action"),
                PermissionDef("seerr.request", "Submit Requests", "Create new media requests", "action"),
            ],
        )

    def _ensure_headers(self) -> None:
        if self.http_client is not None:
            self.http_client.headers["X-Api-Key"] = self.config.get("api_key", "")

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        self._ensure_headers()
        try:
            start = datetime.utcnow()
            resp = await self.http_client.get("/api/v1/status")
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if resp.status_code == 401:
                return HealthResult(status=HealthStatus.DOWN, message="Invalid API key", response_time_ms=elapsed)
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
                    message="Not a Seerr instance (unexpected response)",
                    response_time_ms=elapsed,
                )
            if "version" not in data:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Not a Seerr instance (missing version field)",
                    response_time_ms=elapsed,
                )
            version = data["version"]

            if elapsed > 3000:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Slow response ({elapsed:.0f}ms)",
                    response_time_ms=elapsed,
                    details={"version": version},
                )

            return HealthResult(
                status=HealthStatus.UP,
                message=f"Connected (v{version})",
                response_time_ms=elapsed,
                details={"version": version},
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
        self._ensure_headers()
        empty = self._empty_summary()
        try:
            results = await asyncio.gather(
                self.http_client.get("/api/v1/status"),
                self.http_client.get(
                    "/api/v1/request",
                    params={"take": 20, "skip": 0, "sort": "added", "filter": "all"},
                ),
                self.http_client.get("/api/v1/request/count"),
                return_exceptions=True,
            )

            status_resp, requests_resp, count_resp = results

            # Parse status
            version = "unknown"
            app_name = "Seerr"
            if not isinstance(status_resp, Exception) and status_resp.status_code == 200:
                status_data = status_resp.json()
                version = status_data.get("version", "unknown")
                if "overseerr" in str(status_data).lower():
                    app_name = "Overseerr"

            # Parse request counts
            request_counts = {
                "pending": 0, "approved": 0, "processing": 0,
                "available": 0, "declined": 0, "total": 0,
            }
            if not isinstance(count_resp, Exception) and count_resp.status_code == 200:
                counts = count_resp.json()
                request_counts = {
                    "pending": counts.get("pending", 0),
                    "approved": counts.get("approved", 0),
                    "processing": counts.get("processing", 0),
                    "available": counts.get("available", 0),
                    "declined": counts.get("declined", 0),
                    "total": counts.get("total", 0),
                }

            # Parse recent requests
            recent_requests: list[dict] = []
            if not isinstance(requests_resp, Exception) and requests_resp.status_code == 200:
                req_data = requests_resp.json()
                for r in req_data.get("results", [])[:5]:
                    media = r.get("media", {})
                    req_by = r.get("requestedBy", {})
                    recent_requests.append({
                        "id": r.get("id"),
                        "type": r.get("type", "unknown"),
                        "media_title": media.get("title", media.get("name", "Unknown")),
                        "media_year": media.get("releaseDate", "")[:4] if media.get("releaseDate") else "",
                        "status": REQUEST_STATUS_MAP.get(r.get("status", 0), "Unknown"),
                        "requested_by": req_by.get("displayName", req_by.get("username", "Unknown")),
                        "requested_at": r.get("createdAt", ""),
                        "media_status": MEDIA_STATUS_MAP.get(media.get("status", 0), "Unknown"),
                    })

            data = {
                "version": version,
                "app_name": app_name,
                "request_counts": request_counts,
                "recent_requests": recent_requests,
            }

            return SummaryResult(data=data, fetched_at=datetime.utcnow())

        except Exception as e:
            logger.warning("seerr_summary_failed", instance_id=self.instance_id, error=str(e))
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "version": None,
                "app_name": None,
                "request_counts": {
                    "pending": 0, "approved": 0, "processing": 0,
                    "available": 0, "declined": 0, "total": 0,
                },
                "recent_requests": [],
            },
            fetched_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    async def get_detail(self) -> DetailResult:
        self._ensure_headers()
        empty = DetailResult(
            data={
                "requests": {"total_records": 0, "records": []},
                "processing_media": [], "pending_media": [],
                "services": {"radarr": [], "sonarr": []},
            },
            fetched_at=datetime.utcnow(),
        )
        try:
            results = await asyncio.gather(
                self.http_client.get(
                    "/api/v1/request",
                    params={"take": 50, "skip": 0, "sort": "added", "filter": "all"},
                ),
                self.http_client.get("/api/v1/request/count"),
                self.http_client.get("/api/v1/settings/radarr"),
                self.http_client.get("/api/v1/settings/sonarr"),
                return_exceptions=True,
            )

            requests_resp, count_resp, radarr_resp, sonarr_resp = results

            # Requests
            records: list[dict] = []
            total_records = 0
            if not isinstance(requests_resp, Exception) and requests_resp.status_code == 200:
                req_data = requests_resp.json()
                total_records = req_data.get("pageInfo", {}).get("results", 0)
                for r in req_data.get("results", []):
                    media = r.get("media", {})
                    req_by = r.get("requestedBy", {})
                    records.append({
                        "id": r.get("id"),
                        "type": r.get("type", "unknown"),
                        "media_title": media.get("title", media.get("name", "Unknown")),
                        "request_status": REQUEST_STATUS_MAP.get(r.get("status", 0), "Unknown"),
                        "media_status": MEDIA_STATUS_MAP.get(media.get("status", 0), "Unknown"),
                        "requested_by": req_by.get("displayName", req_by.get("username", "Unknown")),
                        "requested_at": r.get("createdAt", ""),
                    })

            # Services
            radarr_services: list[dict] = []
            if not isinstance(radarr_resp, Exception) and radarr_resp.status_code == 200:
                for s in radarr_resp.json():
                    radarr_services.append({
                        "id": s.get("id"),
                        "name": s.get("name", "Radarr"),
                        "is_default": s.get("isDefault", False),
                        "is_4k": s.get("is4k", False),
                        "hostname": s.get("hostname", ""),
                        "port": s.get("port", 0),
                    })

            sonarr_services: list[dict] = []
            if not isinstance(sonarr_resp, Exception) and sonarr_resp.status_code == 200:
                for s in sonarr_resp.json():
                    sonarr_services.append({
                        "id": s.get("id"),
                        "name": s.get("name", "Sonarr"),
                        "is_default": s.get("isDefault", False),
                        "is_4k": s.get("is4k", False),
                        "hostname": s.get("hostname", ""),
                        "port": s.get("port", 0),
                    })

            return DetailResult(
                data={
                    "requests": {"total_records": total_records, "records": records},
                    "services": {"radarr": radarr_services, "sonarr": sonarr_services},
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning("seerr_detail_failed", instance_id=self.instance_id, error=str(e))
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="approve_request",
                display_name="Approve Request",
                permission="seerr.approve",
                category="action",
                params_schema={
                    "properties": {"request_id": {"type": "integer", "required": True}}
                },
            ),
            ActionDefinition(
                key="decline_request",
                display_name="Decline Request",
                permission="seerr.approve",
                category="action",
                params_schema={
                    "properties": {"request_id": {"type": "integer", "required": True}}
                },
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        self._ensure_headers()
        try:
            if action == "approve_request":
                request_id = int(params.get("request_id", 0))
                if not request_id:
                    return ActionResult(success=False, message="No request ID provided")
                resp = await self.http_client.post(f"/api/v1/request/{request_id}/approve")
                if resp.status_code == 200:
                    return ActionResult(success=True, message="Request approved", invalidate_cache=True)
                return ActionResult(success=False, message=f"Approve failed: HTTP {resp.status_code}")

            elif action == "decline_request":
                request_id = int(params.get("request_id", 0))
                if not request_id:
                    return ActionResult(success=False, message="No request ID provided")
                resp = await self.http_client.post(f"/api/v1/request/{request_id}/decline")
                if resp.status_code == 200:
                    return ActionResult(success=True, message="Request declined", invalidate_cache=True)
                return ActionResult(success=False, message=f"Decline failed: HTTP {resp.status_code}")

            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")

        except (ValueError, TypeError) as e:
            return ActionResult(success=False, message=f"Invalid parameters: {str(e)}")
        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    # ------------------------------------------------------------------
    # Validate Config
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        self._ensure_headers()
        try:
            resp = await self.http_client.get("/api/v1/status")
            if resp.status_code == 401:
                return False, "Invalid API key"
            if resp.status_code != 200:
                return False, f"Unexpected response: HTTP {resp.status_code}"

            data = resp.json()
            version = data.get("version", "unknown")
            app_name = "Seerr"
            if "overseerr" in str(data).lower():
                app_name = "Overseerr"

            return True, f"Connected to {app_name} v{version}"

        except httpx.ConnectError:
            return False, f"Cannot connect to {self.config['url']}"
        except httpx.TimeoutException:
            return False, f"Connection timed out to {self.config['url']}"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
