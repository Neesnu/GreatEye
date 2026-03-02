"""qBittorrent provider — connects to qBittorrent Web API v2.x."""

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
from src.utils.formatting import format_bytes, format_eta, format_speed

logger = structlog.get_logger()

# qBittorrent torrent state → display category mapping
# Covers both v4.x (paused*) and v5.x (stopped*) naming
STATE_MAP: dict[str, str] = {
    "downloading": "Downloading",
    "stalledDL": "Stalled (DL)",
    "uploading": "Seeding",
    "stalledUP": "Seeding (idle)",
    "pausedDL": "Paused",
    "pausedUP": "Paused",
    "stoppedDL": "Paused",
    "stoppedUP": "Paused",
    "queuedDL": "Queued",
    "queuedUP": "Queued",
    "checkingDL": "Checking",
    "checkingUP": "Checking",
    "checkingResumeData": "Checking",
    "forcedDL": "Forced Download",
    "forcedUP": "Forced Upload",
    "error": "Error",
    "missingFiles": "Error",
    "moving": "Moving",
    "unknown": "Unknown",
    "allocating": "Allocating",
    "metaDL": "Metadata",
    "forcedMetaDL": "Metadata",
}

# States that count as actively downloading
_DOWNLOADING_STATES = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "allocating"}
# States that count as actively uploading/seeding
_UPLOADING_STATES = {"uploading", "forcedUP"}
# States that count as paused
_PAUSED_STATES = {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}
# States that count as errored
_ERROR_STATES = {"error", "missingFiles"}


class QBittorrentProvider(BaseProvider):
    """qBittorrent Web API v2.x provider."""

    def __init__(self, instance_id: int, display_name: str, config: dict[str, Any]) -> None:
        super().__init__(instance_id, display_name, config)
        self._sid: str | None = None
        self._qbit_version: str | None = None
        self._is_v5: bool = False

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="qbittorrent",
            display_name="qBittorrent",
            icon="qbittorrent",
            category="download_client",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "qBittorrent URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.45:8080",
                        "help_text": "Full URL to qBittorrent Web UI",
                    },
                    {
                        "key": "username",
                        "label": "Username",
                        "type": "string",
                        "required": False,
                        "help_text": "Leave blank if authentication is disabled",
                    },
                    {
                        "key": "password",
                        "label": "Password",
                        "type": "secret",
                        "required": False,
                    },
                    {
                        "key": "recent_limit",
                        "label": "Recent Transfers Limit",
                        "type": "integer",
                        "required": False,
                        "default": 30,
                        "min": 10,
                        "max": 100,
                        "help_text": "Maximum number of recent transfers to display",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 60,
                "detail_cache_seconds": 30,
            },
            permissions=[
                PermissionDef("qbittorrent.view", "View Downloads", "See transfer list and stats", "read"),
                PermissionDef("qbittorrent.pause", "Pause / Resume Torrents", "Pause or resume transfers", "action"),
                PermissionDef("qbittorrent.delete", "Delete Torrents", "Remove torrents (+ files opt)", "admin"),
                PermissionDef("qbittorrent.speed", "Set Speed Limits", "Toggle alt speed, set limits", "action"),
            ],
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> bool:
        """Authenticate with qBittorrent and store the SID cookie.

        Returns True if auth succeeded or no auth needed, False on failure.
        """
        username = self.config.get("username", "")
        password = self.config.get("password", "")

        # No credentials — assume auth is disabled
        if not username and not password:
            return True

        try:
            resp = await self.http_client.post(
                "/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.status_code == 200 and resp.text.strip().upper() == "OK.":
                self._sid = resp.cookies.get("SID")
                return True
            return False
        except Exception as e:
            logger.warning(
                "qbit_auth_failed",
                instance_id=self.instance_id,
                error=str(e),
            )
            return False

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        """Make an authenticated request, re-authenticating on 403 once."""
        cookies = {"SID": self._sid} if self._sid else {}
        kwargs: dict[str, Any] = {"cookies": cookies}
        if params:
            kwargs["params"] = params
        if data is not None:
            kwargs["data"] = data

        if method == "GET":
            resp = await self.http_client.get(path, **kwargs)
        else:
            resp = await self.http_client.post(path, **kwargs)

        if resp.status_code == 403 and retry_auth:
            if await self._authenticate():
                return await self._request(
                    method, path, data=data, params=params, retry_auth=False
                )

        return resp

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        try:
            # Authenticate first if needed
            if self._sid is None and (self.config.get("username") or self.config.get("password")):
                if not await self._authenticate():
                    return HealthResult(
                        status=HealthStatus.DOWN,
                        message="Authentication failed",
                    )

            start = datetime.utcnow()
            resp = await self._request("GET", "/api/v2/app/version")
            elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000

            if resp.status_code == 403:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Authentication failed",
                    response_time_ms=elapsed_ms,
                )

            if resp.status_code == 200:
                version = resp.text.strip()
                # Validate this is actually a qBittorrent version response
                if not version.startswith("v") or len(version) > 20 or "<" in version:
                    return HealthResult(
                        status=HealthStatus.DOWN,
                        message="Not a qBittorrent instance (unexpected response)",
                        response_time_ms=elapsed_ms,
                    )
                self._qbit_version = version
                # Detect v5.x for API compatibility
                self._is_v5 = self._detect_v5(version)

                if elapsed_ms > 3000:
                    return HealthResult(
                        status=HealthStatus.DEGRADED,
                        message=f"Slow response ({int(elapsed_ms)}ms)",
                        response_time_ms=elapsed_ms,
                        details={"version": version},
                    )

                return HealthResult(
                    status=HealthStatus.UP,
                    message=f"Connected ({version})",
                    response_time_ms=elapsed_ms,
                    details={"version": version},
                )

            return HealthResult(
                status=HealthStatus.DOWN,
                message=f"Unexpected status: {resp.status_code}",
                response_time_ms=elapsed_ms,
            )

        except httpx.TimeoutException:
            return HealthResult(status=HealthStatus.DOWN, message="Connection timed out")
        except httpx.ConnectError:
            return HealthResult(status=HealthStatus.DOWN, message="Connection refused")
        except Exception as e:
            return HealthResult(status=HealthStatus.DOWN, message=str(e))

    @staticmethod
    def _detect_v5(version_str: str) -> bool:
        """Check if the qBittorrent version is v5.x or later."""
        # Version string is like "v4.6.5" or "v5.1.4"
        stripped = version_str.lstrip("v")
        try:
            major = int(stripped.split(".")[0])
            return major >= 5
        except (ValueError, IndexError):
            return False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_summary(self) -> SummaryResult:
        empty = self._empty_summary()
        try:
            recent_limit = self.config.get("recent_limit", 30)
            transfer_coro = self._request("GET", "/api/v2/transfer/info")
            torrents_coro = self._request(
                "GET",
                "/api/v2/torrents/info",
                params={"sort": "added_on", "reverse": "true", "limit": str(recent_limit)},
            )
            transfer_resp, torrents_resp = await asyncio.gather(
                transfer_coro, torrents_coro, return_exceptions=True
            )

            # Handle exceptions from gather
            if isinstance(transfer_resp, Exception) or isinstance(torrents_resp, Exception):
                return empty

            if transfer_resp.status_code != 200 or torrents_resp.status_code != 200:
                return empty

            transfer = transfer_resp.json()
            torrents = torrents_resp.json()

            return self._build_summary(transfer, torrents)

        except Exception as e:
            logger.warning(
                "qbit_summary_failed",
                instance_id=self.instance_id,
                error=str(e),
            )
            return empty

    def _build_summary(self, transfer: dict, torrents: list[dict]) -> SummaryResult:
        """Build summary result from transfer info and torrent list."""
        dl_speed = transfer.get("dl_info_speed", 0)
        up_speed = transfer.get("up_info_speed", 0)

        # Count torrents by state category
        active_downloads = 0
        active_uploads = 0
        paused = 0
        errored = 0
        for t in torrents:
            state = t.get("state", "unknown")
            if state in _DOWNLOADING_STATES:
                active_downloads += 1
            elif state in _UPLOADING_STATES:
                active_uploads += 1
            elif state in _PAUSED_STATES:
                paused += 1
            if state in _ERROR_STATES:
                errored += 1

        # Build recent torrents list
        recent = []
        for t in torrents[:5]:
            recent.append(self._format_torrent_summary(t))

        data = {
            "global_download_speed": dl_speed,
            "global_upload_speed": up_speed,
            "global_download_speed_formatted": format_speed(dl_speed),
            "global_upload_speed_formatted": format_speed(up_speed),
            "active_downloads": active_downloads,
            "active_uploads": active_uploads,
            "paused": paused,
            "errored": errored,
            "total_torrents": len(torrents),
            "free_disk_space": transfer.get("free_space_on_disk", 0),
            "free_disk_space_formatted": format_bytes(transfer.get("free_space_on_disk", 0)),
            "alt_speed_enabled": transfer.get("use_alt_speed_limits", False),
            "connection_status": transfer.get("connection_status", "unknown"),
            "recent_torrents": recent,
        }

        return SummaryResult(data=data, fetched_at=datetime.utcnow())

    def _format_torrent_summary(self, torrent: dict) -> dict[str, Any]:
        """Format a single torrent for summary card display."""
        state = torrent.get("state", "unknown")
        dl_speed = torrent.get("dlspeed", 0)
        up_speed = torrent.get("upspeed", 0)
        eta = torrent.get("eta", 0)
        # qBit uses 8640000 as "infinity" ETA
        if eta and eta >= 8640000:
            eta = -1

        return {
            "hash": torrent.get("hash", ""),
            "name": torrent.get("name", "Unknown"),
            "state": state,
            "state_display": STATE_MAP.get(state, "Unknown"),
            "progress": torrent.get("progress", 0),
            "size": torrent.get("size", 0),
            "size_formatted": format_bytes(torrent.get("size", 0)),
            "download_speed": dl_speed,
            "download_speed_formatted": format_speed(dl_speed),
            "upload_speed": up_speed,
            "upload_speed_formatted": format_speed(up_speed),
            "eta": eta,
            "eta_formatted": format_eta(eta) if eta >= 0 else "∞",
            "category": torrent.get("category", ""),
            "tags": torrent.get("tags", ""),
            "added_on": torrent.get("added_on", 0),
            "ratio": torrent.get("ratio", 0),
        }

    def _empty_summary(self) -> SummaryResult:
        """Return empty summary when data is unavailable."""
        return SummaryResult(
            data={
                "global_download_speed": None,
                "global_upload_speed": None,
                "global_download_speed_formatted": "—",
                "global_upload_speed_formatted": "—",
                "active_downloads": None,
                "active_uploads": None,
                "paused": None,
                "errored": None,
                "total_torrents": None,
                "free_disk_space": None,
                "free_disk_space_formatted": "—",
                "alt_speed_enabled": None,
                "connection_status": None,
                "recent_torrents": [],
            },
            fetched_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    async def get_detail(self) -> DetailResult:
        empty = DetailResult(
            data={"transfer": {}, "torrents": [], "categories": {}, "tags": []},
            fetched_at=datetime.utcnow(),
        )
        try:
            recent_limit = self.config.get("recent_limit", 30)
            transfer_coro = self._request("GET", "/api/v2/transfer/info")
            torrents_coro = self._request(
                "GET",
                "/api/v2/torrents/info",
                params={"sort": "added_on", "reverse": "true", "limit": str(recent_limit)},
            )
            maindata_coro = self._request("GET", "/api/v2/sync/maindata")

            results = await asyncio.gather(
                transfer_coro, torrents_coro, maindata_coro, return_exceptions=True
            )

            for r in results:
                if isinstance(r, Exception):
                    return empty

            transfer_resp, torrents_resp, maindata_resp = results

            transfer = transfer_resp.json() if transfer_resp.status_code == 200 else {}
            torrents_raw = torrents_resp.json() if torrents_resp.status_code == 200 else []
            maindata = maindata_resp.json() if maindata_resp.status_code == 200 else {}

            # Build transfer info
            transfer_data = {
                "download_speed": transfer.get("dl_info_speed", 0),
                "upload_speed": transfer.get("up_info_speed", 0),
                "download_session": transfer.get("dl_info_data", 0),
                "upload_session": transfer.get("up_info_data", 0),
                "download_total": transfer.get("alltime_dl", 0),
                "upload_total": transfer.get("alltime_ul", 0),
                "free_disk_space": transfer.get("free_space_on_disk", 0),
                "dht_nodes": transfer.get("dht_nodes", 0),
                "connection_status": transfer.get("connection_status", "unknown"),
                "alt_speed_enabled": transfer.get("use_alt_speed_limits", False),
                "download_limit": transfer.get("dl_rate_limit", 0),
                "upload_limit": transfer.get("up_rate_limit", 0),
            }

            # Build torrent list
            torrents = []
            for t in torrents_raw:
                state = t.get("state", "unknown")
                dl_speed = t.get("dlspeed", 0)
                up_speed = t.get("upspeed", 0)
                eta_val = t.get("eta", 0)
                if eta_val and eta_val >= 8640000:
                    eta_val = -1

                torrents.append({
                    "hash": t.get("hash", ""),
                    "name": t.get("name", "Unknown"),
                    "state": state,
                    "state_display": STATE_MAP.get(state, "Unknown"),
                    "progress": t.get("progress", 0),
                    "size": t.get("size", 0),
                    "size_formatted": format_bytes(t.get("size", 0)),
                    "downloaded": t.get("downloaded", 0),
                    "downloaded_formatted": format_bytes(t.get("downloaded", 0)),
                    "uploaded": t.get("uploaded", 0),
                    "uploaded_formatted": format_bytes(t.get("uploaded", 0)),
                    "download_speed": dl_speed,
                    "download_speed_formatted": format_speed(dl_speed),
                    "upload_speed": up_speed,
                    "upload_speed_formatted": format_speed(up_speed),
                    "eta": eta_val,
                    "eta_formatted": format_eta(eta_val) if eta_val >= 0 else "∞",
                    "ratio": t.get("ratio", 0),
                    "category": t.get("category", ""),
                    "tags": t.get("tags", ""),
                    "added_on": t.get("added_on", 0),
                    "completion_on": t.get("completion_on"),
                    "tracker": _extract_tracker(t.get("tracker", "")),
                    "num_seeds": t.get("num_seeds", 0),
                    "num_leeches": t.get("num_leechs", 0),
                    "save_path": t.get("save_path", ""),
                    "content_path": t.get("content_path", ""),
                })

            categories = maindata.get("categories", {})
            tags = maindata.get("tags", [])

            return DetailResult(
                data={
                    "transfer": transfer_data,
                    "torrents": torrents,
                    "categories": categories,
                    "tags": tags if isinstance(tags, list) else [],
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning(
                "qbit_detail_failed",
                instance_id=self.instance_id,
                error=str(e),
            )
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="pause",
                display_name="Pause",
                permission="qbittorrent.pause",
                category="action",
                params_schema={
                    "properties": {
                        "hashes": {"type": "string", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="resume",
                display_name="Resume",
                permission="qbittorrent.pause",
                category="action",
                params_schema={
                    "properties": {
                        "hashes": {"type": "string", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="delete",
                display_name="Delete",
                permission="qbittorrent.delete",
                category="admin",
                confirm=True,
                confirm_message="Delete torrent(s)? This cannot be undone.",
                params_schema={
                    "properties": {
                        "hashes": {"type": "string", "required": True},
                        "delete_files": {"type": "string", "required": False},
                    }
                },
            ),
            ActionDefinition(
                key="toggle_alt_speed",
                display_name="Toggle Alt Speed",
                permission="qbittorrent.speed",
                category="action",
            ),
            ActionDefinition(
                key="set_download_limit",
                display_name="Set Download Limit",
                permission="qbittorrent.speed",
                category="action",
                params_schema={
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "required": True,
                            "min": 0,
                        },
                    }
                },
            ),
            ActionDefinition(
                key="set_upload_limit",
                display_name="Set Upload Limit",
                permission="qbittorrent.speed",
                category="action",
                params_schema={
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "required": True,
                            "min": 0,
                        },
                    }
                },
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        try:
            if action == "pause":
                return await self._action_pause_resume(params, pause=True)
            elif action == "resume":
                return await self._action_pause_resume(params, pause=False)
            elif action == "delete":
                return await self._action_delete(params)
            elif action == "toggle_alt_speed":
                return await self._action_toggle_alt_speed()
            elif action == "set_download_limit":
                return await self._action_set_speed_limit(params, direction="download")
            elif action == "set_upload_limit":
                return await self._action_set_speed_limit(params, direction="upload")
            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")
        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    async def _action_pause_resume(
        self, params: dict[str, Any], *, pause: bool
    ) -> ActionResult:
        hashes = params.get("hashes", "")
        if not hashes:
            return ActionResult(success=False, message="No hashes provided")

        # v5.x uses stop/start instead of pause/resume
        if pause:
            endpoint = "/api/v2/torrents/stop" if self._is_v5 else "/api/v2/torrents/pause"
            word = "paused"
        else:
            endpoint = "/api/v2/torrents/start" if self._is_v5 else "/api/v2/torrents/resume"
            word = "resumed"

        resp = await self._request("POST", endpoint, data={"hashes": hashes})
        if resp.status_code == 200:
            return ActionResult(success=True, message=f"Torrents {word}")
        return ActionResult(success=False, message=f"Failed ({resp.status_code})")

    async def _action_delete(self, params: dict[str, Any]) -> ActionResult:
        hashes = params.get("hashes", "")
        if not hashes:
            return ActionResult(success=False, message="No hashes provided")

        delete_files = str(params.get("delete_files", "false")).lower() == "true"
        resp = await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={
                "hashes": hashes,
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        if resp.status_code == 200:
            msg = "Torrents deleted"
            if delete_files:
                msg += " (files removed)"
            return ActionResult(success=True, message=msg)
        return ActionResult(success=False, message=f"Delete failed ({resp.status_code})")

    async def _action_toggle_alt_speed(self) -> ActionResult:
        resp = await self._request("POST", "/api/v2/transfer/toggleSpeedLimitsMode")
        if resp.status_code == 200:
            return ActionResult(success=True, message="Alt speed toggled")
        return ActionResult(success=False, message=f"Toggle failed ({resp.status_code})")

    async def _action_set_speed_limit(
        self, params: dict[str, Any], *, direction: str
    ) -> ActionResult:
        limit = params.get("limit", 0)
        if direction == "download":
            endpoint = "/api/v2/transfer/setDownloadLimit"
        else:
            endpoint = "/api/v2/transfer/setUploadLimit"

        resp = await self._request("POST", endpoint, data={"limit": str(limit)})
        if resp.status_code == 200:
            label = format_speed(limit) if limit > 0 else "unlimited"
            return ActionResult(
                success=True,
                message=f"{direction.title()} limit set to {label}",
            )
        return ActionResult(
            success=False, message=f"Set limit failed ({resp.status_code})"
        )

    # ------------------------------------------------------------------
    # Validate Config
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        try:
            authed = await self._authenticate()
            if not authed and (self.config.get("username") or self.config.get("password")):
                return False, "Authentication failed — check username and password"

            resp = await self._request("GET", "/api/v2/app/version")
            if resp.status_code == 200:
                version = resp.text.strip()
                return True, f"Connected ({version})"
            elif resp.status_code == 403:
                return False, "Authentication failed — check username and password"
            else:
                return False, "Connected but unexpected response — is this a qBittorrent instance?"

        except httpx.ConnectError:
            url = self.config.get("url", "unknown")
            return False, f"Cannot connect to {url}"
        except httpx.TimeoutException:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self) -> None:
        self._sid = None
        self._qbit_version = None


def _extract_tracker(tracker_url: str) -> str:
    """Extract hostname from tracker URL."""
    if not tracker_url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(tracker_url)
        return parsed.hostname or tracker_url
    except Exception:
        return tracker_url
