"""Radarr provider — connects to Radarr API v3 for movie management."""

import asyncio
import json as json_mod
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


class RadarrProvider(ArrBaseProvider):
    """Radarr movie management provider."""

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="radarr",
            display_name="Radarr",
            icon="radarr",
            category="media",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "Radarr URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.45:7878",
                        "help_text": "Full URL to Radarr web UI (include /radarr if using URL base)",
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
                PermissionDef("radarr.view", "View Radarr Data", "Library, queue, calendar, health", "read"),
                PermissionDef("radarr.search", "Search for Movies", "Trigger automatic movie search", "action"),
                PermissionDef("radarr.refresh", "Refresh Movie", "Refresh movie metadata/disk scan", "action"),
                PermissionDef("radarr.delete", "Delete Movie", "Remove movie (+ files optionally)", "admin"),
                PermissionDef("radarr.import", "Manual Import", "Import downloaded files", "action"),
            ],
        )

    def _expected_app_name(self) -> str:
        return "Radarr"

    def _queue_include_params(self) -> dict[str, Any]:
        return {"includeMovie": True}

    def _normalize_queue_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Normalize a Radarr queue record."""
        movie = record.get("movie", {})
        quality = record.get("quality", {}).get("quality", {})
        size = record.get("size", 0)
        sizeleft = record.get("sizeleft", 0)
        progress = ((size - sizeleft) / size * 100) if size > 0 else 0.0

        year = movie.get("year", 0)
        detail_title = f"({year})" if year else ""

        status_messages = record.get("statusMessages", [])
        error_msg = None
        if status_messages:
            msgs = []
            for sm in status_messages:
                msgs.extend(sm.get("messages", []))
            error_msg = "; ".join(msgs) if msgs else None

        return {
            "id": record.get("id"),
            "media_title": movie.get("title", "Unknown Movie"),
            "detail_title": detail_title,
            "movie_id": movie.get("id"),
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
            "download_id": record.get("downloadId", ""),
        }

    def _normalize_manual_import_file(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single manual import preview file for Radarr."""
        movie = raw.get("movie") or {}
        quality = raw.get("quality", {})
        languages = raw.get("languages", [])
        rejections = raw.get("rejections", [])

        return {
            "path": raw.get("path", ""),
            "relative_path": raw.get("relativePath", ""),
            "name": raw.get("name", ""),
            "size": raw.get("size", 0),
            "size_formatted": format_bytes(raw.get("size", 0)),
            "movie_id": movie.get("id"),
            "movie_title": movie.get("title", "Unknown"),
            "movie_year": movie.get("year", 0),
            "quality": quality,
            "quality_name": quality.get("quality", {}).get("name", "Unknown"),
            "languages": languages,
            "language_names": [lang.get("name", "Unknown") for lang in languages],
            "release_group": raw.get("releaseGroup", ""),
            "rejections": rejections,
            "has_rejections": len(rejections) > 0,
            "download_id": raw.get("downloadId", ""),
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
            month_end = (now + timedelta(days=30)).strftime("%Y-%m-%d")

            movie_coro = self.http_client.get(f"{self.api_base}/movie")
            queue_coro = self._fetch_queue(page=1, page_size=20)
            calendar_coro = self.http_client.get(
                f"{self.api_base}/calendar",
                params={"start": today, "end": month_end},
            )
            health_coro = self.http_client.get(f"{self.api_base}/health")

            results = await asyncio.gather(
                movie_coro, queue_coro, calendar_coro, health_coro,
                return_exceptions=True,
            )

            for r in results:
                if isinstance(r, Exception):
                    logger.warning("radarr_summary_partial", error=str(r))

            movie_resp, queue_data, calendar_resp, health_resp = results

            # Parse movies
            movie_list: list[dict] = []
            if not isinstance(movie_resp, Exception) and movie_resp.status_code == 200:
                movie_list = movie_resp.json()

            movie_count = len(movie_list)
            movie_monitored = sum(1 for m in movie_list if m.get("monitored", False))
            movie_downloaded = sum(1 for m in movie_list if m.get("hasFile", False))
            missing_count = sum(
                1 for m in movie_list
                if m.get("monitored", False) and not m.get("hasFile", False)
            )
            size_on_disk = sum(
                m.get("sizeOnDisk", m.get("movieFile", {}).get("size", 0))
                for m in movie_list
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

            # Parse calendar
            calendar_upcoming: list[dict] = []
            if not isinstance(calendar_resp, Exception) and calendar_resp.status_code == 200:
                for m in calendar_resp.json()[:5]:
                    calendar_upcoming.append({
                        "title": m.get("title", ""),
                        "year": m.get("year", 0),
                        "in_cinemas": m.get("inCinemas", ""),
                        "physical_release": m.get("physicalRelease", ""),
                        "digital_release": m.get("digitalRelease", ""),
                        "monitored": m.get("monitored", False),
                        "has_file": m.get("hasFile", False),
                    })

            # Parse health
            health_warnings: list[dict] = []
            if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
                health_warnings = health_resp.json()

            data = {
                "movie_count": movie_count,
                "movie_monitored": movie_monitored,
                "movie_downloaded": movie_downloaded,
                "missing_count": missing_count,
                "size_on_disk": size_on_disk,
                "size_on_disk_formatted": format_bytes(size_on_disk),
                "queue": {
                    "total": queue_total,
                    "downloading": queue_downloading,
                    "errors": queue_errors,
                    "records": queue_records[:5],
                },
                "calendar_upcoming": calendar_upcoming,
                "health_warnings": health_warnings,
            }

            return SummaryResult(data=data, fetched_at=datetime.utcnow())

        except Exception as e:
            logger.warning(
                "radarr_summary_failed",
                instance_id=self.instance_id,
                error=str(e),
            )
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "movie_count": None,
                "movie_monitored": None,
                "movie_downloaded": None,
                "missing_count": None,
                "size_on_disk": None,
                "size_on_disk_formatted": "—",
                "queue": {"total": 0, "downloading": 0, "errors": 0, "records": []},
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
                "movies": [], "queue": {"total_records": 0, "records": []},
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
                self.http_client.get(f"{self.api_base}/movie"),
                self._fetch_queue(page=1, page_size=50),
                self.http_client.get(
                    f"{self.api_base}/calendar",
                    params={"start": past_week, "end": month_end},
                ),
                self.http_client.get(f"{self.api_base}/health"),
                self._fetch_disk_space(),
                self.http_client.get(f"{self.api_base}/rootfolder"),
                self.http_client.get(f"{self.api_base}/qualityprofile"),
                self.http_client.get(f"{self.api_base}/tag"),
                return_exceptions=True,
            )

            (
                movie_resp, queue_data, calendar_resp,
                health_resp, disk_space, rootfolder_resp, profiles_resp, tags_resp,
            ) = results

            # Movies
            movies: list[dict] = []
            if not isinstance(movie_resp, Exception) and movie_resp.status_code == 200:
                for m in movie_resp.json():
                    size = m.get("sizeOnDisk", m.get("movieFile", {}).get("size", 0))
                    movies.append({
                        "id": m.get("id"),
                        "title": m.get("title", "Unknown"),
                        "sort_title": m.get("sortTitle", ""),
                        "year": m.get("year", 0),
                        "status": m.get("status", "unknown"),
                        "overview": m.get("overview", ""),
                        "studio": m.get("studio", ""),
                        "quality_profile_id": m.get("qualityProfileId"),
                        "tags": m.get("tags", []),
                        "size_on_disk": size,
                        "size_on_disk_formatted": format_bytes(size),
                        "monitored": m.get("monitored", False),
                        "has_file": m.get("hasFile", False),
                        "path": m.get("path", ""),
                        "in_cinemas": m.get("inCinemas", ""),
                        "physical_release": m.get("physicalRelease", ""),
                        "digital_release": m.get("digitalRelease", ""),
                    })

            # Queue
            if isinstance(queue_data, Exception):
                queue_data = {"total_records": 0, "records": []}

            # Calendar
            calendar_list: list[dict] = []
            if not isinstance(calendar_resp, Exception) and calendar_resp.status_code == 200:
                for m in calendar_resp.json():
                    calendar_list.append({
                        "movie_id": m.get("id"),
                        "title": m.get("title", ""),
                        "year": m.get("year", 0),
                        "in_cinemas": m.get("inCinemas", ""),
                        "physical_release": m.get("physicalRelease", ""),
                        "digital_release": m.get("digitalRelease", ""),
                        "monitored": m.get("monitored", False),
                        "has_file": m.get("hasFile", False),
                    })

            # Health
            health_list: list[dict] = []
            if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
                health_list = health_resp.json()

            # Disk space
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
                    "movies": movies,
                    "queue": queue_data,
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
                "radarr_detail_failed",
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
                key="search_movie",
                display_name="Search Movie",
                permission="radarr.search",
                category="action",
                params_schema={
                    "properties": {
                        "movie_ids": {"type": "string", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="search_missing",
                display_name="Search All Missing",
                permission="radarr.search",
                category="action",
                confirm=True,
                confirm_message="Search for all missing movies? This may trigger many downloads.",
            ),
            ActionDefinition(
                key="refresh_movie",
                display_name="Refresh Movie",
                permission="radarr.refresh",
                category="action",
                params_schema={
                    "properties": {
                        "movie_id": {"type": "integer", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="delete_movie",
                display_name="Delete Movie",
                permission="radarr.delete",
                category="admin",
                confirm=True,
                confirm_message="Delete this movie from Radarr? This cannot be undone.",
                params_schema={
                    "properties": {
                        "movie_id": {"type": "integer", "required": True},
                        "delete_files": {"type": "string", "required": False},
                    }
                },
            ),
            ActionDefinition(
                key="remove_from_queue",
                display_name="Remove from Queue",
                permission="radarr.search",
                category="action",
                params_schema={
                    "properties": {
                        "queue_id": {"type": "integer", "required": True},
                        "blocklist": {"type": "string", "required": False},
                    }
                },
            ),
            ActionDefinition(
                key="grab_queue_item",
                display_name="Grab Release",
                permission="radarr.search",
                category="action",
                params_schema={
                    "properties": {
                        "queue_id": {"type": "integer", "required": True},
                    }
                },
            ),
            ActionDefinition(
                key="manual_import",
                display_name="Manual Import",
                permission="radarr.import",
                category="action",
                params_schema={
                    "properties": {
                        "file_count": {"type": "string", "required": True},
                    }
                },
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        try:
            if action == "search_movie":
                ids = params.get("movie_ids", "")
                if not ids:
                    return ActionResult(success=False, message="No movie IDs provided")
                movie_ids = [int(x.strip()) for x in str(ids).split(",")]
                return await self._execute_command("MoviesSearch", movieIds=movie_ids)

            elif action == "search_missing":
                return await self._execute_command("MissingMoviesSearch")

            elif action == "refresh_movie":
                movie_id = int(params.get("movie_id", 0))
                if not movie_id:
                    return ActionResult(success=False, message="No movie ID provided")
                return await self._execute_command("RefreshMovie", movieId=movie_id)

            elif action == "delete_movie":
                return await self._action_delete_movie(params)

            elif action == "remove_from_queue":
                queue_id = int(params.get("queue_id", 0))
                if not queue_id:
                    return ActionResult(success=False, message="No queue ID provided")
                blocklist = str(params.get("blocklist", "false")).lower() == "true"
                return await self._remove_from_queue(queue_id, blocklist=blocklist)

            elif action == "grab_queue_item":
                queue_id = int(params.get("queue_id", 0))
                if not queue_id:
                    return ActionResult(success=False, message="No queue ID provided")
                return await self._grab_queue_item(queue_id)

            elif action == "manual_import":
                return await self._build_and_execute_manual_import(params)

            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")

        except (ValueError, TypeError) as e:
            return ActionResult(success=False, message=f"Invalid parameters: {str(e)}")
        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    async def _action_delete_movie(self, params: dict[str, Any]) -> ActionResult:
        movie_id = int(params.get("movie_id", 0))
        if not movie_id:
            return ActionResult(success=False, message="No movie ID provided")

        delete_files = str(params.get("delete_files", "false")).lower() == "true"

        try:
            response = await self.http_client.delete(
                f"{self.api_base}/movie/{movie_id}",
                params={"deleteFiles": delete_files},
            )
            if response.status_code in (200, 204):
                msg = "Movie deleted"
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

    async def _build_and_execute_manual_import(
        self, params: dict[str, Any]
    ) -> ActionResult:
        """Parse form params and build ManualImport command payload."""
        file_count = int(params.get("file_count", 0))
        if file_count == 0:
            return ActionResult(success=False, message="No files provided")

        import_mode = params.get("import_mode", "auto")
        files: list[dict[str, Any]] = []

        for i in range(file_count):
            if not params.get(f"file_enabled_{i}"):
                continue

            path = params.get(f"file_path_{i}", "")
            movie_id = int(params.get(f"movie_id_{i}", 0))
            quality_json = params.get(f"file_quality_{i}", "{}")
            languages_json = params.get(f"file_languages_{i}", "[]")
            release_group = params.get(f"file_release_group_{i}", "")
            download_id = params.get(f"file_download_id_{i}", "")

            if not path or not movie_id:
                continue

            try:
                quality = json_mod.loads(quality_json) if isinstance(quality_json, str) else quality_json
                languages = json_mod.loads(languages_json) if isinstance(languages_json, str) else languages_json
            except (json_mod.JSONDecodeError, TypeError):
                continue

            files.append({
                "path": path,
                "movieId": movie_id,
                "quality": quality,
                "languages": languages,
                "releaseGroup": release_group,
                "downloadId": download_id,
            })

        if not files:
            return ActionResult(success=False, message="No valid files selected")

        return await self._execute_manual_import(files, import_mode)
