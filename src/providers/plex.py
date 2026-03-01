"""Plex Media Server provider — connects to Plex API for library and session monitoring."""

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
from src.utils.formatting import format_bytes

logger = structlog.get_logger()


class PlexProvider(BaseProvider):
    """Plex Media Server provider."""

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="plex",
            display_name="Plex",
            icon="plex",
            category="media",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "Plex URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.45:32400",
                        "help_text": "Full URL to Plex Media Server (include port 32400)",
                    },
                    {
                        "key": "api_key",
                        "label": "Plex Token",
                        "type": "secret",
                        "required": True,
                        "help_text": "X-Plex-Token — found in Plex URL or via account settings",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 60,
                "detail_cache_seconds": 120,
            },
            permissions=[
                PermissionDef("plex.view", "View Plex Data", "Libraries, sessions, server info", "read"),
                PermissionDef("plex.scan", "Scan Libraries", "Trigger library scan", "action"),
                PermissionDef("plex.manage", "Manage Sessions", "Terminate streams", "admin"),
            ],
        )

    def _ensure_headers(self) -> None:
        if self.http_client is not None:
            self.http_client.headers["X-Plex-Token"] = self.config.get("api_key", "")
            self.http_client.headers["Accept"] = "application/json"

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        self._ensure_headers()
        try:
            start = datetime.utcnow()
            resp = await self.http_client.get("/identity")
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if resp.status_code == 401:
                return HealthResult(status=HealthStatus.DOWN, message="Invalid Plex token", response_time_ms=elapsed)
            if resp.status_code != 200:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message=f"Unexpected status: {resp.status_code}",
                    response_time_ms=elapsed,
                )

            data = resp.json()
            mc = data.get("MediaContainer", {})
            version = mc.get("version", "unknown")
            name = mc.get("friendlyName", "Plex")

            if elapsed > 3000:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Slow response ({elapsed:.0f}ms)",
                    response_time_ms=elapsed,
                    details={"version": version, "name": name},
                )

            return HealthResult(
                status=HealthStatus.UP,
                message=f"Connected — {name} (v{version})",
                response_time_ms=elapsed,
                details={"version": version, "name": name},
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
                self.http_client.get("/library/sections"),
                self.http_client.get("/status/sessions"),
                return_exceptions=True,
            )

            sections_resp, sessions_resp = results

            # Parse libraries
            libraries: list[dict] = []
            if not isinstance(sections_resp, Exception) and sections_resp.status_code == 200:
                mc = sections_resp.json().get("MediaContainer", {})
                for lib in mc.get("Directory", []):
                    libraries.append({
                        "id": lib.get("key"),
                        "title": lib.get("title", "Unknown"),
                        "type": lib.get("type", "unknown"),
                        "count": lib.get("count", 0),
                    })

            # Parse sessions
            sessions: list[dict] = []
            stream_count = 0
            transcode_count = 0
            if not isinstance(sessions_resp, Exception) and sessions_resp.status_code == 200:
                mc = sessions_resp.json().get("MediaContainer", {})
                stream_count = mc.get("size", 0)
                for s in mc.get("Metadata", []):
                    user = s.get("User", {})
                    player = s.get("Player", {})
                    session = s.get("Session", {})
                    media = s.get("Media", [{}])[0] if s.get("Media") else {}
                    parts = media.get("Part", [{}])[0] if media.get("Part") else {}
                    is_transcode = parts.get("decision", "directplay") == "transcode"
                    if is_transcode:
                        transcode_count += 1

                    sessions.append({
                        "session_id": session.get("id", ""),
                        "user": user.get("title", "Unknown"),
                        "title": s.get("title", "Unknown"),
                        "grandparent_title": s.get("grandparentTitle", ""),
                        "type": s.get("type", "unknown"),
                        "state": player.get("state", "unknown"),
                        "player": player.get("product", "Unknown"),
                        "platform": player.get("platform", "Unknown"),
                        "progress": int(s.get("viewOffset", 0)) / max(int(s.get("duration", 1)), 1) * 100,
                        "is_transcode": is_transcode,
                        "quality": media.get("videoResolution", ""),
                    })

            data = {
                "library_count": len(libraries),
                "libraries": libraries,
                "stream_count": stream_count,
                "transcode_count": transcode_count,
                "sessions": sessions,
            }

            return SummaryResult(data=data, fetched_at=datetime.utcnow())

        except Exception as e:
            logger.warning("plex_summary_failed", instance_id=self.instance_id, error=str(e))
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "library_count": None,
                "libraries": [],
                "stream_count": None,
                "transcode_count": None,
                "sessions": [],
            },
            fetched_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    async def get_detail(self) -> DetailResult:
        self._ensure_headers()
        empty = DetailResult(
            data={"libraries": [], "sessions": [], "server": {}},
            fetched_at=datetime.utcnow(),
        )
        try:
            results = await asyncio.gather(
                self.http_client.get("/library/sections"),
                self.http_client.get("/status/sessions"),
                self.http_client.get("/identity"),
                return_exceptions=True,
            )

            sections_resp, sessions_resp, identity_resp = results

            # Libraries
            libraries: list[dict] = []
            if not isinstance(sections_resp, Exception) and sections_resp.status_code == 200:
                mc = sections_resp.json().get("MediaContainer", {})
                for lib in mc.get("Directory", []):
                    libraries.append({
                        "id": lib.get("key"),
                        "title": lib.get("title", "Unknown"),
                        "type": lib.get("type", "unknown"),
                        "agent": lib.get("agent", ""),
                        "scanner": lib.get("scanner", ""),
                        "language": lib.get("language", ""),
                        "count": lib.get("count", 0),
                    })

            # Sessions
            sessions: list[dict] = []
            if not isinstance(sessions_resp, Exception) and sessions_resp.status_code == 200:
                mc = sessions_resp.json().get("MediaContainer", {})
                for s in mc.get("Metadata", []):
                    user = s.get("User", {})
                    player = s.get("Player", {})
                    session = s.get("Session", {})
                    media = s.get("Media", [{}])[0] if s.get("Media") else {}
                    parts = media.get("Part", [{}])[0] if media.get("Part") else {}

                    sessions.append({
                        "session_id": session.get("id", ""),
                        "user": user.get("title", "Unknown"),
                        "title": s.get("title", "Unknown"),
                        "grandparent_title": s.get("grandparentTitle", ""),
                        "parent_title": s.get("parentTitle", ""),
                        "type": s.get("type", "unknown"),
                        "state": player.get("state", "unknown"),
                        "player": player.get("product", "Unknown"),
                        "platform": player.get("platform", "Unknown"),
                        "address": player.get("address", ""),
                        "progress": int(s.get("viewOffset", 0)) / max(int(s.get("duration", 1)), 1) * 100,
                        "duration": s.get("duration", 0),
                        "view_offset": s.get("viewOffset", 0),
                        "is_transcode": parts.get("decision", "directplay") == "transcode",
                        "video_resolution": media.get("videoResolution", ""),
                        "video_codec": media.get("videoCodec", ""),
                        "audio_codec": media.get("audioCodec", ""),
                        "bandwidth": session.get("bandwidth", 0),
                    })

            # Server info
            server: dict = {}
            if not isinstance(identity_resp, Exception) and identity_resp.status_code == 200:
                mc = identity_resp.json().get("MediaContainer", {})
                server = {
                    "name": mc.get("friendlyName", ""),
                    "version": mc.get("version", ""),
                    "platform": mc.get("platform", ""),
                    "machine_identifier": mc.get("machineIdentifier", ""),
                }

            return DetailResult(
                data={
                    "libraries": libraries,
                    "sessions": sessions,
                    "server": server,
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning("plex_detail_failed", instance_id=self.instance_id, error=str(e))
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="scan_library",
                display_name="Scan Library",
                permission="plex.scan",
                category="action",
                params_schema={
                    "properties": {"section_id": {"type": "string", "required": True}}
                },
            ),
            ActionDefinition(
                key="kill_stream",
                display_name="Terminate Stream",
                permission="plex.manage",
                category="admin",
                confirm=True,
                confirm_message="Terminate this stream? The user will be disconnected.",
                params_schema={
                    "properties": {"session_id": {"type": "string", "required": True}}
                },
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        self._ensure_headers()
        try:
            if action == "scan_library":
                section_id = params.get("section_id", "")
                if not section_id:
                    return ActionResult(success=False, message="No section ID provided")
                resp = await self.http_client.get(f"/library/sections/{section_id}/refresh")
                if resp.status_code == 200:
                    return ActionResult(success=True, message="Library scan started", invalidate_cache=True)
                return ActionResult(success=False, message=f"Scan failed: HTTP {resp.status_code}")

            elif action == "kill_stream":
                session_id = params.get("session_id", "")
                if not session_id:
                    return ActionResult(success=False, message="No session ID provided")
                resp = await self.http_client.get(
                    "/status/sessions/terminate",
                    params={"sessionId": session_id, "reason": "Terminated by admin"},
                )
                if resp.status_code == 200:
                    return ActionResult(success=True, message="Stream terminated", invalidate_cache=True)
                return ActionResult(success=False, message=f"Terminate failed: HTTP {resp.status_code}")

            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")

        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    # ------------------------------------------------------------------
    # Validate Config
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        self._ensure_headers()
        try:
            resp = await self.http_client.get("/identity")
            if resp.status_code == 401:
                return False, "Invalid Plex token"
            if resp.status_code != 200:
                return False, f"Unexpected response: HTTP {resp.status_code}"

            data = resp.json()
            mc = data.get("MediaContainer", {})
            name = mc.get("friendlyName", "Plex")
            version = mc.get("version", "unknown")

            return True, f"Connected to {name} (v{version})"

        except httpx.ConnectError:
            return False, f"Cannot connect to {self.config['url']}"
        except httpx.TimeoutException:
            return False, f"Connection timed out to {self.config['url']}"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
