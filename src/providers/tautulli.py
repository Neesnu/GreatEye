"""Tautulli provider — connects to Tautulli API for Plex monitoring and statistics."""

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


class TautulliProvider(BaseProvider):
    """Tautulli Plex monitoring provider."""

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="tautulli",
            display_name="Tautulli",
            icon="tautulli",
            category="media",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "Tautulli URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.45:8181",
                        "help_text": "Full URL to Tautulli web UI",
                    },
                    {
                        "key": "api_key",
                        "label": "API Key",
                        "type": "secret",
                        "required": True,
                        "help_text": "Found in Settings → Web Interface → API Key",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 60,
                "detail_cache_seconds": 120,
            },
            permissions=[
                PermissionDef("tautulli.view", "View Tautulli Data", "Activity, history, statistics", "read"),
                PermissionDef("tautulli.manage", "Manage Tautulli", "Refresh libraries, restart", "action"),
            ],
        )

    async def _api_call(self, cmd: str, **params: Any) -> dict[str, Any]:
        """Make a Tautulli API call using the cmd= pattern."""
        query: dict[str, Any] = {
            "apikey": self.config.get("api_key", ""),
            "cmd": cmd,
        }
        query.update(params)
        resp = await self.http_client.get("/api/v2", params=query)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        response = data.get("response", {})
        if response.get("result") != "success":
            return {}
        return response.get("data", {})

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        try:
            start = datetime.utcnow()
            data = await self._api_call("server_info")
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if not data:
                # Try to determine if it's an auth error
                resp = await self.http_client.get(
                    "/api/v2",
                    params={"apikey": self.config.get("api_key", ""), "cmd": "server_info"},
                )
                if resp.status_code == 401:
                    return HealthResult(status=HealthStatus.DOWN, message="Invalid API key", response_time_ms=elapsed)
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Failed to get server info",
                    response_time_ms=elapsed,
                )

            version = data.get("tautulli_version", "unknown")
            pms_name = data.get("pms_name", "Plex")

            if elapsed > 3000:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Slow response ({elapsed:.0f}ms)",
                    response_time_ms=elapsed,
                    details={"version": version, "pms_name": pms_name},
                )

            return HealthResult(
                status=HealthStatus.UP,
                message=f"Connected — {pms_name} (v{version})",
                response_time_ms=elapsed,
                details={"version": version, "pms_name": pms_name},
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
            results = await asyncio.gather(
                self._api_call("get_activity"),
                self._api_call("get_recently_added", count=5),
                self._api_call("get_libraries"),
                return_exceptions=True,
            )

            activity_data, recent_data, libraries_data = results

            # Parse activity
            stream_count = 0
            transcode_count = 0
            total_bandwidth = 0
            sessions: list[dict] = []
            if isinstance(activity_data, dict) and activity_data:
                stream_count = activity_data.get("stream_count", 0)
                if isinstance(stream_count, str):
                    stream_count = int(stream_count) if stream_count.isdigit() else 0
                total_bandwidth = activity_data.get("total_bandwidth", 0)
                transcode_count = activity_data.get("stream_count_transcode", 0)
                if isinstance(transcode_count, str):
                    transcode_count = int(transcode_count) if transcode_count.isdigit() else 0

                for s in activity_data.get("sessions", []):
                    sessions.append({
                        "session_id": s.get("session_id", ""),
                        "user": s.get("friendly_name", "Unknown"),
                        "title": s.get("full_title", s.get("title", "Unknown")),
                        "state": s.get("state", "unknown"),
                        "player": s.get("player", "Unknown"),
                        "quality": s.get("quality_profile", ""),
                        "progress_percent": s.get("progress_percent", "0"),
                        "transcode_decision": s.get("transcode_decision", "direct play"),
                    })

            # Parse recently added
            recently_added: list[dict] = []
            if isinstance(recent_data, dict) and recent_data:
                for item in recent_data.get("recently_added", [])[:5]:
                    recently_added.append({
                        "title": item.get("title", "Unknown"),
                        "parent_title": item.get("parent_title", ""),
                        "grandparent_title": item.get("grandparent_title", ""),
                        "media_type": item.get("media_type", "unknown"),
                        "added_at": item.get("added_at", ""),
                        "year": item.get("year", ""),
                    })

            # Parse libraries
            library_count = 0
            if isinstance(libraries_data, dict) and libraries_data:
                library_count = len(libraries_data) if isinstance(libraries_data, list) else 0

            data = {
                "stream_count": stream_count,
                "transcode_count": transcode_count,
                "total_bandwidth": total_bandwidth,
                "sessions": sessions,
                "recently_added": recently_added,
                "library_count": library_count,
            }

            return SummaryResult(data=data, fetched_at=datetime.utcnow())

        except Exception as e:
            logger.warning("tautulli_summary_failed", instance_id=self.instance_id, error=str(e))
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "stream_count": None,
                "transcode_count": None,
                "total_bandwidth": None,
                "sessions": [],
                "recently_added": [],
                "library_count": None,
            },
            fetched_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    async def get_detail(self) -> DetailResult:
        empty = DetailResult(
            data={"activity": {}, "recently_added": [], "libraries": [], "history": []},
            fetched_at=datetime.utcnow(),
        )
        try:
            results = await asyncio.gather(
                self._api_call("get_activity"),
                self._api_call("get_recently_added", count=20),
                self._api_call("get_libraries"),
                self._api_call("get_history", length=50),
                return_exceptions=True,
            )

            activity_data, recent_data, libraries_data, history_data = results

            # Activity
            activity: dict = {}
            if isinstance(activity_data, dict):
                activity = activity_data

            # Recently added
            recently_added: list[dict] = []
            if isinstance(recent_data, dict):
                for item in recent_data.get("recently_added", []):
                    recently_added.append({
                        "title": item.get("title", "Unknown"),
                        "parent_title": item.get("parent_title", ""),
                        "grandparent_title": item.get("grandparent_title", ""),
                        "media_type": item.get("media_type", "unknown"),
                        "added_at": item.get("added_at", ""),
                        "year": item.get("year", ""),
                        "library_name": item.get("library_name", ""),
                    })

            # Libraries
            libraries: list[dict] = []
            if isinstance(libraries_data, list):
                for lib in libraries_data:
                    libraries.append({
                        "section_id": lib.get("section_id", ""),
                        "section_name": lib.get("section_name", "Unknown"),
                        "section_type": lib.get("section_type", "unknown"),
                        "count": lib.get("count", 0),
                        "parent_count": lib.get("parent_count", 0),
                        "child_count": lib.get("child_count", 0),
                    })

            # History
            history: list[dict] = []
            if isinstance(history_data, dict):
                for h in history_data.get("data", []):
                    history.append({
                        "id": h.get("id"),
                        "date": h.get("date", ""),
                        "user": h.get("friendly_name", "Unknown"),
                        "title": h.get("full_title", h.get("title", "Unknown")),
                        "media_type": h.get("media_type", "unknown"),
                        "duration": h.get("duration", 0),
                        "play_duration": h.get("play_duration", 0),
                        "paused_counter": h.get("paused_counter", 0),
                        "watched_status": h.get("watched_status", 0),
                        "player": h.get("player", "Unknown"),
                        "platform": h.get("platform", "Unknown"),
                    })

            return DetailResult(
                data={
                    "activity": activity,
                    "recently_added": recently_added,
                    "libraries": libraries,
                    "history": history,
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning("tautulli_detail_failed", instance_id=self.instance_id, error=str(e))
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="refresh_libraries",
                display_name="Refresh Libraries",
                permission="tautulli.manage",
                category="action",
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        try:
            if action == "refresh_libraries":
                data = await self._api_call("refresh_libraries_list")
                # Tautulli returns empty data on success
                return ActionResult(success=True, message="Libraries refreshed", invalidate_cache=True)
            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")
        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    # ------------------------------------------------------------------
    # Validate Config
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        try:
            data = await self._api_call("server_info")
            if not data:
                return False, "Cannot authenticate — check API key"

            version = data.get("tautulli_version", "unknown")
            pms_name = data.get("pms_name", "Plex")
            return True, f"Connected to Tautulli v{version} ({pms_name})"

        except httpx.ConnectError:
            return False, f"Cannot connect to {self.config['url']}"
        except httpx.TimeoutException:
            return False, f"Connection timed out to {self.config['url']}"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
