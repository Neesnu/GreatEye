"""Shared base class for *arr family providers (Sonarr, Radarr, Prowlarr).

Handles health checks, queue fetching, command execution, disk space,
and config validation. Child classes implement summary, detail, and actions.
"""

import asyncio
from abc import abstractmethod
from datetime import datetime
from typing import Any

import httpx
import structlog

from src.providers.base import (
    ActionResult,
    BaseProvider,
    HealthResult,
    HealthStatus,
    ProviderMeta,
)
from src.utils.formatting import format_bytes

logger = structlog.get_logger()


class ArrBaseProvider(BaseProvider):
    """Abstract base for all *arr family providers."""

    api_version: str = "v3"

    @property
    def api_base(self) -> str:
        return f"/api/{self.api_version}"

    @staticmethod
    @abstractmethod
    def meta() -> ProviderMeta:
        ...

    @abstractmethod
    def _expected_app_name(self) -> str:
        """Return the expected appName value (e.g. 'Sonarr', 'Radarr')."""
        ...

    def _validate_app_name(self, app_name: str) -> bool:
        """Check if the response appName matches what we expect."""
        return self._expected_app_name().lower() in app_name.lower()

    def _ensure_headers(self) -> None:
        """Set the API key header on the http_client (called lazily)."""
        if self.http_client is not None:
            self.http_client.headers["X-Api-Key"] = self.config.get("api_key", "")

    @abstractmethod
    def _queue_include_params(self) -> dict[str, Any]:
        """Return provider-specific include params for queue endpoint."""
        ...

    @abstractmethod
    def _normalize_queue_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single queue record to the standard shape."""
        ...

    # ------------------------------------------------------------------
    # Health Check (shared)
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        self._ensure_headers()
        try:
            start = datetime.utcnow()
            status_resp, health_resp = await asyncio.gather(
                self.http_client.get(f"{self.api_base}/system/status"),
                self.http_client.get(f"{self.api_base}/health"),
                return_exceptions=True,
            )
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            # Handle status response
            if isinstance(status_resp, Exception):
                raise status_resp
            if status_resp.status_code == 401:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Invalid API key",
                    response_time_ms=elapsed,
                )
            if status_resp.status_code != 200:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message=f"Unexpected status: {status_resp.status_code}",
                    response_time_ms=elapsed,
                )

            status_data = status_resp.json()
            version = status_data.get("version", "unknown")
            app_name = status_data.get("appName", "unknown")

            # Validate this is the expected app
            if not self._validate_app_name(app_name):
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message=f"Expected {self._expected_app_name()}, got {app_name}",
                    response_time_ms=elapsed,
                )

            # Process health warnings
            health_issues: list[dict] = []
            if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
                health_issues = health_resp.json()

            errors = [h for h in health_issues if h.get("type") == "error"]
            warnings = [h for h in health_issues if h.get("type") == "warning"]

            # Determine final status
            if elapsed > 3000:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Slow response ({elapsed:.0f}ms)",
                    response_time_ms=elapsed,
                    details={"version": version, "health": health_issues},
                )
            if errors:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Connected with {len(errors)} error(s)",
                    response_time_ms=elapsed,
                    details={"version": version, "health": health_issues},
                )
            if warnings:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Connected with {len(warnings)} warning(s)",
                    response_time_ms=elapsed,
                    details={"version": version, "health": health_issues},
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
    # Queue Fetching (shared)
    # ------------------------------------------------------------------

    async def _fetch_queue(
        self, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        """Fetch download queue with pagination."""
        params: dict[str, Any] = {
            "page": page,
            "pageSize": page_size,
        }
        params.update(self._queue_include_params())

        try:
            response = await self.http_client.get(
                f"{self.api_base}/queue", params=params
            )
            if response.status_code != 200:
                return {"total_records": 0, "records": []}

            data = response.json()
            return {
                "total_records": data.get("totalRecords", 0),
                "records": [
                    self._normalize_queue_record(r)
                    for r in data.get("records", [])
                ],
            }
        except Exception:
            return {"total_records": 0, "records": []}

    # ------------------------------------------------------------------
    # Command Execution (shared)
    # ------------------------------------------------------------------

    async def _execute_command(self, command_name: str, **params: Any) -> ActionResult:
        """Execute an *arr command. Fire-and-forget pattern."""
        payload: dict[str, Any] = {"name": command_name, **params}

        try:
            response = await self.http_client.post(
                f"{self.api_base}/command", json=payload
            )
            if response.status_code in (200, 201):
                return ActionResult(
                    success=True,
                    message=f"{command_name} started",
                    data=response.json(),
                    invalidate_cache=True,
                )
            else:
                return ActionResult(
                    success=False,
                    message=f"Command rejected: HTTP {response.status_code}",
                )
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Command failed: {str(e)}",
            )

    # ------------------------------------------------------------------
    # Remove from Queue (shared)
    # ------------------------------------------------------------------

    async def _remove_from_queue(
        self, queue_id: int, blocklist: bool = False
    ) -> ActionResult:
        """Remove an item from the download queue."""
        try:
            response = await self.http_client.delete(
                f"{self.api_base}/queue/{queue_id}",
                params={"removeFromClient": True, "blocklist": blocklist},
            )
            if response.status_code in (200, 204):
                msg = "Removed from queue"
                if blocklist:
                    msg += " and added to blocklist"
                return ActionResult(success=True, message=msg, invalidate_cache=True)
            else:
                return ActionResult(
                    success=False,
                    message=f"Remove failed: HTTP {response.status_code}",
                )
        except Exception as e:
            return ActionResult(success=False, message=f"Remove failed: {str(e)}")

    # ------------------------------------------------------------------
    # Grab Queue Item (shared)
    # ------------------------------------------------------------------

    async def _grab_queue_item(self, queue_id: int) -> ActionResult:
        """Grab/re-grab a queue item, overriding rejection rules."""
        try:
            response = await self.http_client.post(
                f"{self.api_base}/queue/grab/{queue_id}"
            )
            if response.status_code in (200, 201):
                return ActionResult(success=True, message="Release grabbed", invalidate_cache=True)
            else:
                return ActionResult(
                    success=False,
                    message=f"Grab failed: HTTP {response.status_code}",
                )
        except Exception as e:
            return ActionResult(success=False, message=f"Grab failed: {str(e)}")

    # ------------------------------------------------------------------
    # Manual Import (shared)
    # ------------------------------------------------------------------

    async def _fetch_manual_import_preview(
        self, download_id: str
    ) -> list[dict[str, Any]]:
        """Fetch manual import file preview for a download.

        GET {api_base}/manualimport?downloadId={download_id}&filterExistingFiles=true
        """
        self._ensure_headers()
        try:
            response = await self.http_client.get(
                f"{self.api_base}/manualimport",
                params={
                    "downloadId": download_id,
                    "filterExistingFiles": True,
                },
            )
            if response.status_code != 200:
                logger.warning(
                    "manual_import_preview_failed",
                    instance_id=self.instance_id,
                    status=response.status_code,
                )
                return []
            return response.json()
        except Exception as e:
            logger.warning(
                "manual_import_preview_error",
                instance_id=self.instance_id,
                error=str(e),
            )
            return []

    async def _execute_manual_import(
        self, files: list[dict[str, Any]], import_mode: str = "auto"
    ) -> ActionResult:
        """Execute a manual import command with user-confirmed file list."""
        return await self._execute_command(
            "ManualImport",
            importMode=import_mode,
            files=files,
        )

    # ------------------------------------------------------------------
    # Disk Space (shared)
    # ------------------------------------------------------------------

    async def _fetch_disk_space(self) -> list[dict[str, Any]]:
        """Fetch disk space info for all mounted paths."""
        try:
            response = await self.http_client.get(f"{self.api_base}/diskspace")
            if response.status_code != 200:
                return []
            return [
                {
                    "path": d.get("path"),
                    "label": d.get("label", d.get("path")),
                    "free_space": d.get("freeSpace", 0),
                    "free_space_formatted": format_bytes(d.get("freeSpace", 0)),
                    "total_space": d.get("totalSpace", 0),
                    "total_space_formatted": format_bytes(d.get("totalSpace", 0)),
                }
                for d in response.json()
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Validate Config (shared)
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        self._ensure_headers()
        try:
            response = await self.http_client.get(f"{self.api_base}/system/status")

            if response.status_code == 401:
                return False, "Invalid API key"
            if response.status_code == 403:
                return False, "Access denied — check API key permissions"
            if response.status_code != 200:
                return False, f"Unexpected response: HTTP {response.status_code}"

            data = response.json()
            app_name = data.get("appName", "unknown")
            version = data.get("version", "unknown")

            if not self._validate_app_name(app_name):
                return False, f"This appears to be {app_name}, not {self._expected_app_name()}"

            return True, f"Connected to {app_name} v{version}"

        except httpx.ConnectError:
            return False, f"Cannot connect to {self.config['url']}"
        except httpx.TimeoutException:
            return False, f"Connection timed out to {self.config['url']}"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
