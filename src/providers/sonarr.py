"""Sonarr provider — connects to Sonarr API v3 for TV series management."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog

from src.providers.arr_base import ArrBaseProvider
from src.providers.base import (
    ActionDefinition,
    ActionResult,
    DetailResult,
    PermissionDef,
    ProviderMeta,
    SummaryResult,
)
from src.utils.formatting import format_bytes

logger = structlog.get_logger()


class SonarrProvider(ArrBaseProvider):
    """Sonarr TV series management provider."""

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="sonarr",
            display_name="Sonarr",
            icon="sonarr",
            category="media",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "Sonarr URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.45:8989",
                        "help_text": "Full URL to Sonarr web UI (include /sonarr if using URL base)",
                    },
                    {
                        "key": "api_key",
                        "label": "API Key",
                        "type": "secret",
                        "required": True,
                        "help_text": "Found in Settings → General → API Key",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 60,
                "detail_cache_seconds": 300,
            },
            permissions=[
                PermissionDef("sonarr.view", "View Sonarr Data", "Library, queue, calendar, health", "read"),
                PermissionDef("sonarr.search", "Search for Episodes", "Trigger automatic episode search", "action"),
                PermissionDef("sonarr.import", "Manual Import", "Import downloaded files", "action"),
                PermissionDef("sonarr.refresh", "Refresh Series", "Refresh series metadata/disk scan", "action"),
                PermissionDef("sonarr.delete", "Delete Series", "Remove series (+ files optionally)", "admin"),
            ],
        )

    def _expected_app_name(self) -> str:
        return "Sonarr"

    def _queue_include_params(self) -> dict[str, Any]:
        return {"includeEpisode": True, "includeSeries": True}

    def _normalize_queue_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Normalize a Sonarr queue record."""
        series = record.get("series", {})
        episode = record.get("episode", {})
        quality = record.get("quality", {}).get("quality", {})
        size = record.get("size", 0)
        sizeleft = record.get("sizeleft", 0)
        progress = ((size - sizeleft) / size * 100) if size > 0 else 0.0

        season_num = episode.get("seasonNumber", 0)
        episode_num = episode.get("episodeNumber", 0)
        episode_title = episode.get("title", "")
        detail_title = f"S{season_num:02d}E{episode_num:02d}"
        if episode_title:
            detail_title += f" - {episode_title}"

        status_messages = record.get("statusMessages", [])
        error_msg = None
        if status_messages:
            msgs = []
            for sm in status_messages:
                msgs.extend(sm.get("messages", []))
            error_msg = "; ".join(msgs) if msgs else None

        return {
            "id": record.get("id"),
            "media_title": series.get("title", "Unknown Series"),
            "detail_title": detail_title,
            "series_id": series.get("id"),
            "episode_id": episode.get("id"),
            "season_number": season_num,
            "episode_number": episode_num,
            "quality": quality.get("name", "Unknown"),
            "custom_formats": [cf.get("name", "") for cf in record.get("customFormats", [])],
            "size": size,
            "size_formatted": format_bytes(size),
            "sizeleft": sizeleft,
            "sizeleft_formatted": format_bytes(sizeleft),
            "progress": round(progress, 1),
            "status": record.get("status", "unknown"),
            "tracked_download_status": record.get("trackedDownloadStatus", ""),
            "tracked_download_state": record.get("trackedDownloadState", ""),
            "status_messages": status_messages,
            "error_message": error_msg,
            "timeleft": record.get("timeleft", ""),
            "download_client": record.get("downloadClient", ""),
            "indexer": record.get("indexer", ""),
            "output_path": record.get("outputPath", ""),
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_summary(self) -> SummaryResult:
        self._ensure_headers()
        empty = self._empty_summary()
        try:
            now = datetime.utcnow()
            today = now.strftime("%Y-%m-%d")
            week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d")

            series_coro = self.http_client.get(f"{self.api_base}/series")
            queue_coro = self._fetch_queue(page=1, page_size=20)
            missing_coro = self.http_client.get(
                f"{self.api_base}/wanted/missing",
                params={"page": 1, "pageSize": 1},
            )
            calendar_coro = self.http_client.get(
                f"{self.api_base}/calendar",
                params={"start": today, "end": week_end},
            )
            health_coro = self.http_client.get(f"{self.api_base}/health")

            results = await asyncio.gather(
                series_coro, queue_coro, missing_coro, calendar_coro, health_coro,
                return_exceptions=True,
            )

            for r in results:
                if isinstance(r, Exception):
                    logger.warning("sonarr_summary_partial", error=str(r))

            series_resp, queue_data, missing_resp, calendar_resp, health_resp = results

            # Parse series
            series_list: list[dict] = []
            if not isinstance(series_resp, Exception) and series_resp.status_code == 200:
                series_list = series_resp.json()

            series_count = len(series_list)
            series_monitored = sum(1 for s in series_list if s.get("monitored", False))
            episode_count = sum(
                s.get("statistics", {}).get("episodeCount", 0) for s in series_list
            )
            episode_file_count = sum(
                s.get("statistics", {}).get("episodeFileCount", 0) for s in series_list
            )
            size_on_disk = sum(
                s.get("statistics", {}).get("sizeOnDisk", 0) for s in series_list
            )

            # Parse queue
            if isinstance(queue_data, Exception):
                queue_data = {"total_records": 0, "records": []}
            queue_total = queue_data.get("total_records", 0)
            queue_records = queue_data.get("records", [])
            queue_downloading = sum(
                1 for r in queue_records if r.get("tracked_download_state") == "downloading"
            )
            queue_errors = sum(
                1 for r in queue_records
                if r.get("tracked_download_status") in ("warning", "error")
                or r.get("error_message")
            )
            queue_warnings = sum(
                1 for r in queue_records
                if r.get("tracked_download_status") == "warning"
            )

            # Parse missing
            missing_count = 0
            if not isinstance(missing_resp, Exception) and missing_resp.status_code == 200:
                missing_count = missing_resp.json().get("totalRecords", 0)

            # Parse calendar
            calendar_upcoming: list[dict] = []
            if not isinstance(calendar_resp, Exception) and calendar_resp.status_code == 200:
                for ep in calendar_resp.json()[:5]:
                    series_info = ep.get("series", {})
                    calendar_upcoming.append({
                        "series_title": series_info.get("title", ep.get("seriesTitle", "")),
                        "episode_title": ep.get("title", ""),
                        "season_number": ep.get("seasonNumber", 0),
                        "episode_number": ep.get("episodeNumber", 0),
                        "air_date_utc": ep.get("airDateUtc", ""),
                        "monitored": ep.get("monitored", False),
                        "has_file": ep.get("hasFile", False),
                    })

            # Parse health
            health_warnings: list[dict] = []
            if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
                health_warnings = health_resp.json()

            data = {
                "series_count": series_count,
                "series_monitored": series_monitored,
                "episode_count": episode_count,
                "episode_file_count": episode_file_count,
                "size_on_disk": size_on_disk,
                "size_on_disk_formatted": format_bytes(size_on_disk),
                "queue": {
                    "total": queue_total,
                    "downloading": queue_downloading,
                    "errors": queue_errors,
                    "warnings": queue_warnings,
                    "items": queue_records[:5],
                },
                "missing_count": missing_count,
                "calendar_upcoming": calendar_upcoming,
                "health_warnings": health_warnings,
            }

            return SummaryResult(data=data, fetched_at=datetime.utcnow())

        except Exception as e:
            logger.warning(
                "sonarr_summary_failed",
                instance_id=self.instance_id,
                error=str(e),
            )
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "series_count": None,
                "series_monitored": None,
                "episode_count": None,
                "episode_file_count": None,
                "size_on_disk": None,
                "size_on_disk_formatted": "—",
                "queue": {"total": 0, "downloading": 0, "errors": 0, "warnings": 0, "items": []},
                "missing_count": None,
                "calendar_upcoming": [],
                "health_warnings": [],
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
                "series": [], "queue": {"total_records": 0, "records": []},
                "missing": {"total_records": 0, "records": []},
                "calendar": [], "health": [], "disk_space": [],
                "root_folders": [], "quality_profiles": [], "tags": [],
            },
            fetched_at=datetime.utcnow(),
        )
        try:
            now = datetime.utcnow()
            past_week = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            month_end = (now + timedelta(days=30)).strftime("%Y-%m-%d")

            results = await asyncio.gather(
                self.http_client.get(f"{self.api_base}/series"),
                self._fetch_queue(page=1, page_size=50),
                self.http_client.get(
                    f"{self.api_base}/wanted/missing",
                    params={
                        "page": 1, "pageSize": 20,
                        "sortKey": "airDateUtc", "sortDirection": "descending",
                    },
                ),
                self.http_client.get(
                    f"{self.api_base}/calendar",
                    params={"start": past_week, "end": month_end, "includeSeries": True},
                ),
                self.http_client.get(f"{self.api_base}/health"),
                self._fetch_disk_space(),
                self.http_client.get(f"{self.api_base}/rootfolder"),
                self.http_client.get(f"{self.api_base}/qualityprofile"),
                self.http_client.get(f"{self.api_base}/tag"),
                return_exceptions=True,
            )

            (
                series_resp, queue_data, missing_resp, calendar_resp,
                health_resp, disk_space, rootfolder_resp, profiles_resp, tags_resp,
            ) = results

            # Series
            series_list: list[dict] = []
            if not isinstance(series_resp, Exception) and series_resp.status_code == 200:
                for s in series_resp.json():
                    stats = s.get("statistics", {})
                    ep_count = stats.get("episodeCount", 0)
                    ep_file_count = stats.get("episodeFileCount", 0)
                    percent = (ep_file_count / ep_count * 100) if ep_count > 0 else 0.0

                    seasons = []
                    for season in s.get("seasons", []):
                        s_stats = season.get("statistics", {})
                        s_ep = s_stats.get("episodeCount", 0)
                        s_file = s_stats.get("episodeFileCount", 0)
                        seasons.append({
                            "season_number": season.get("seasonNumber", 0),
                            "monitored": season.get("monitored", False),
                            "episode_count": s_ep,
                            "episode_file_count": s_file,
                            "total_episode_count": s_stats.get("totalEpisodeCount", 0),
                            "percent_of_episodes": round(s_file / s_ep * 100, 1) if s_ep > 0 else 0.0,
                        })

                    series_list.append({
                        "id": s.get("id"),
                        "title": s.get("title", "Unknown"),
                        "sort_title": s.get("sortTitle", ""),
                        "status": s.get("status", "unknown"),
                        "overview": s.get("overview", ""),
                        "network": s.get("network", ""),
                        "year": s.get("year", 0),
                        "seasons": seasons,
                        "quality_profile_id": s.get("qualityProfileId"),
                        "tags": s.get("tags", []),
                        "size_on_disk": stats.get("sizeOnDisk", 0),
                        "size_on_disk_formatted": format_bytes(stats.get("sizeOnDisk", 0)),
                        "monitored": s.get("monitored", False),
                        "episode_count": ep_count,
                        "episode_file_count": ep_file_count,
                        "percent_complete": round(percent, 1),
                        "path": s.get("path", ""),
                    })

            # Queue
            if isinstance(queue_data, Exception):
                queue_data = {"total_records": 0, "records": []}

            # Missing
            missing_data: dict[str, Any] = {"total_records": 0, "records": []}
            if not isinstance(missing_resp, Exception) and missing_resp.status_code == 200:
                raw = missing_resp.json()
                missing_records = []
                for r in raw.get("records", []):
                    series_info = r.get("series", {})
                    missing_records.append({
                        "series_id": series_info.get("id", r.get("seriesId")),
                        "series_title": series_info.get("title", ""),
                        "episode_id": r.get("id"),
                        "episode_title": r.get("title", ""),
                        "season_number": r.get("seasonNumber", 0),
                        "episode_number": r.get("episodeNumber", 0),
                        "air_date_utc": r.get("airDateUtc", ""),
                        "monitored": r.get("monitored", False),
                    })
                missing_data = {
                    "total_records": raw.get("totalRecords", 0),
                    "records": missing_records,
                }

            # Calendar
            calendar_list: list[dict] = []
            if not isinstance(calendar_resp, Exception) and calendar_resp.status_code == 200:
                for ep in calendar_resp.json():
                    series_info = ep.get("series", {})
                    calendar_list.append({
                        "series_id": series_info.get("id", ep.get("seriesId")),
                        "series_title": series_info.get("title", ""),
                        "episode_id": ep.get("id"),
                        "episode_title": ep.get("title", ""),
                        "season_number": ep.get("seasonNumber", 0),
                        "episode_number": ep.get("episodeNumber", 0),
                        "air_date_utc": ep.get("airDateUtc", ""),
                        "monitored": ep.get("monitored", False),
                        "has_file": ep.get("hasFile", False),
                    })

            # Health
            health_list: list[dict] = []
            if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
                health_list = health_resp.json()

            # Disk space (already processed by _fetch_disk_space)
            if isinstance(disk_space, Exception):
                disk_space = []

            # Root folders
            root_folders: list[dict] = []
            if not isinstance(rootfolder_resp, Exception) and rootfolder_resp.status_code == 200:
                root_folders = [
                    {"path": rf.get("path", ""), "free_space": rf.get("freeSpace", 0)}
                    for rf in rootfolder_resp.json()
                ]

            # Quality profiles
            quality_profiles: list[dict] = []
            if not isinstance(profiles_resp, Exception) and profiles_resp.status_code == 200:
                quality_profiles = [
                    {"id": p.get("id"), "name": p.get("name", "")}
                    for p in profiles_resp.json()
                ]

            # Tags
            tags: list[dict] = []
            if not isinstance(tags_resp, Exception) and tags_resp.status_code == 200:
                tags = [
                    {"id": t.get("id"), "label": t.get("label", "")}
                    for t in tags_resp.json()
                ]

            return DetailResult(
                data={
                    "series": series_list,
                    "queue": queue_data,
                    "missing": missing_data,
                    "calendar": calendar_list,
                    "health": health_list,
                    "disk_space": disk_space,
                    "root_folders": root_folders,
                    "quality_profiles": quality_profiles,
                    "tags": tags,
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning(
                "sonarr_detail_failed",
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
                key="search_episode",
                display_name="Search Episode",
                permission="sonarr.search",
                category="action",
                params_schema={
                    "properties": {
                        "episode_ids": {"type": "string", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="search_season",
                display_name="Search Season",
                permission="sonarr.search",
                category="action",
                params_schema={
                    "properties": {
                        "series_id": {"type": "integer", "required": True},
                        "season_number": {"type": "integer", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="search_series",
                display_name="Search Series",
                permission="sonarr.search",
                category="action",
                params_schema={
                    "properties": {
                        "series_id": {"type": "integer", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="search_missing",
                display_name="Search All Missing",
                permission="sonarr.search",
                category="action",
                confirm=True,
                confirm_message="Search for all missing episodes? This may trigger many downloads.",
            ),
            ActionDefinition(
                key="refresh_series",
                display_name="Refresh Series",
                permission="sonarr.refresh",
                category="action",
                params_schema={
                    "properties": {
                        "series_id": {"type": "integer", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="delete_series",
                display_name="Delete Series",
                permission="sonarr.delete",
                category="admin",
                confirm=True,
                confirm_message="Delete this series from Sonarr? This cannot be undone.",
                params_schema={
                    "properties": {
                        "series_id": {"type": "integer", "required": True},
                        "delete_files": {"type": "string", "required": False},
                    }
                },
            ),
            ActionDefinition(
                key="remove_from_queue",
                display_name="Remove from Queue",
                permission="sonarr.search",
                category="action",
                params_schema={
                    "properties": {
                        "queue_id": {"type": "integer", "required": True},
                        "blocklist": {"type": "string", "required": False},
                    }
                },
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        try:
            if action == "search_episode":
                ids = params.get("episode_ids", "")
                if not ids:
                    return ActionResult(success=False, message="No episode IDs provided")
                episode_ids = [int(x.strip()) for x in str(ids).split(",")]
                return await self._execute_command("EpisodeSearch", episodeIds=episode_ids)

            elif action == "search_season":
                series_id = int(params.get("series_id", 0))
                season_number = int(params.get("season_number", 0))
                if not series_id:
                    return ActionResult(success=False, message="No series ID provided")
                return await self._execute_command(
                    "SeasonSearch", seriesId=series_id, seasonNumber=season_number
                )

            elif action == "search_series":
                series_id = int(params.get("series_id", 0))
                if not series_id:
                    return ActionResult(success=False, message="No series ID provided")
                return await self._execute_command("SeriesSearch", seriesId=series_id)

            elif action == "search_missing":
                return await self._execute_command("MissingEpisodeSearch")

            elif action == "refresh_series":
                series_id = int(params.get("series_id", 0))
                if not series_id:
                    return ActionResult(success=False, message="No series ID provided")
                return await self._execute_command("RefreshSeries", seriesId=series_id)

            elif action == "delete_series":
                return await self._action_delete_series(params)

            elif action == "remove_from_queue":
                queue_id = int(params.get("queue_id", 0))
                if not queue_id:
                    return ActionResult(success=False, message="No queue ID provided")
                blocklist = str(params.get("blocklist", "false")).lower() == "true"
                return await self._remove_from_queue(queue_id, blocklist=blocklist)

            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")

        except (ValueError, TypeError) as e:
            return ActionResult(success=False, message=f"Invalid parameters: {str(e)}")
        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    async def _action_delete_series(self, params: dict[str, Any]) -> ActionResult:
        series_id = int(params.get("series_id", 0))
        if not series_id:
            return ActionResult(success=False, message="No series ID provided")

        delete_files = str(params.get("delete_files", "false")).lower() == "true"

        try:
            response = await self.http_client.delete(
                f"{self.api_base}/series/{series_id}",
                params={"deleteFiles": delete_files},
            )
            if response.status_code in (200, 204):
                msg = "Series deleted"
                if delete_files:
                    msg += " (files removed)"
                return ActionResult(success=True, message=msg, invalidate_cache=True)
            else:
                return ActionResult(
                    success=False,
                    message=f"Delete failed: HTTP {response.status_code}",
                )
        except Exception as e:
            return ActionResult(success=False, message=f"Delete failed: {str(e)}")
