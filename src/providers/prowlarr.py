"""Prowlarr provider — connects to Prowlarr API v1 for indexer management."""

import asyncio
from datetime import datetime
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

logger = structlog.get_logger()


class ProwlarrProvider(ArrBaseProvider):
    """Prowlarr indexer management provider."""

    api_version: str = "v1"

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="prowlarr",
            display_name="Prowlarr",
            icon="prowlarr",
            category="media",
            config_schema={
                "fields": [
                    {
                        "key": "url",
                        "label": "Prowlarr URL",
                        "type": "url",
                        "required": True,
                        "placeholder": "http://10.0.0.45:9696",
                        "help_text": "Full URL to Prowlarr web UI",
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
                "health_seconds": 60,
                "summary_seconds": 120,
                "detail_cache_seconds": 300,
            },
            permissions=[
                PermissionDef("prowlarr.view", "View Prowlarr Data", "Indexer status and stats", "read"),
                PermissionDef("prowlarr.search", "Search Indexers", "Trigger a manual search", "action"),
                PermissionDef("prowlarr.test", "Test Indexers", "Test indexer connectivity", "action"),
                PermissionDef("prowlarr.sync", "Sync with Apps", "Trigger app sync", "action"),
            ],
        )

    def _expected_app_name(self) -> str:
        return "Prowlarr"

    def _queue_include_params(self) -> dict[str, Any]:
        return {}

    def _normalize_queue_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return record

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_summary(self) -> SummaryResult:
        self._ensure_headers()
        empty = self._empty_summary()
        try:
            results = await asyncio.gather(
                self.http_client.get(f"{self.api_base}/indexer"),
                self.http_client.get(f"{self.api_base}/indexerstats"),
                self.http_client.get(f"{self.api_base}/health"),
                self.http_client.get(f"{self.api_base}/application"),
                return_exceptions=True,
            )

            indexer_resp, stats_resp, health_resp, apps_resp = results

            # Parse indexers
            indexers_raw: list[dict] = []
            if not isinstance(indexer_resp, Exception) and indexer_resp.status_code == 200:
                indexers_raw = indexer_resp.json()

            # Parse stats
            stats_data: dict = {}
            if not isinstance(stats_resp, Exception) and stats_resp.status_code == 200:
                stats_data = stats_resp.json()

            # Build per-indexer stats map
            indexer_stats: dict[int, dict] = {}
            for idx_stat in stats_data.get("indexers", []):
                indexer_stats[idx_stat.get("indexerId", 0)] = idx_stat

            # Build indexer list with derived status
            indexers: list[dict] = []
            healthy = 0
            failing = 0
            disabled = 0
            total_grabs = 0
            total_queries = 0

            for idx in indexers_raw:
                idx_id = idx.get("id", 0)
                enabled = idx.get("enable", True)
                stats = indexer_stats.get(idx_id, {})

                queries = stats.get("numberOfQueries", 0)
                grabs = stats.get("numberOfGrabs", 0)
                failures = stats.get("numberOfFailedQueries", 0) + stats.get("numberOfFailedGrabs", 0)
                avg_rt = stats.get("averageResponseTime", 0)

                total_grabs += grabs
                total_queries += queries

                if not enabled:
                    status = "disabled"
                    disabled += 1
                elif failures > 0 and queries > 0 and failures / max(queries, 1) > 0.5:
                    status = "failing"
                    failing += 1
                elif failures > 0:
                    status = "degraded"
                    healthy += 1
                else:
                    status = "healthy"
                    healthy += 1

                indexers.append({
                    "id": idx_id,
                    "name": idx.get("name", "Unknown"),
                    "protocol": idx.get("protocol", "unknown"),
                    "enabled": enabled,
                    "status": status,
                    "average_response_time_ms": avg_rt,
                    "number_of_grabs": grabs,
                    "number_of_queries": queries,
                    "number_of_failures": failures,
                })

            # Parse health
            health_warnings: list[dict] = []
            if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
                health_warnings = health_resp.json()

            # Parse apps
            apps: list[dict] = []
            if not isinstance(apps_resp, Exception) and apps_resp.status_code == 200:
                for app in apps_resp.json():
                    apps.append({
                        "id": app.get("id"),
                        "name": app.get("name", "Unknown"),
                        "sync_level": app.get("syncLevel", "unknown"),
                    })

            data = {
                "indexer_count": len(indexers_raw),
                "indexers_healthy": healthy,
                "indexers_failing": failing,
                "indexers_disabled": disabled,
                "total_grabs_today": total_grabs,
                "total_queries_today": total_queries,
                "indexers": indexers,
                "apps": apps,
                "health_warnings": health_warnings,
            }

            return SummaryResult(data=data, fetched_at=datetime.utcnow())

        except Exception as e:
            logger.warning("prowlarr_summary_failed", instance_id=self.instance_id, error=str(e))
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "indexer_count": None,
                "indexers_healthy": None,
                "indexers_failing": None,
                "indexers_disabled": None,
                "total_grabs_today": None,
                "total_queries_today": None,
                "indexers": [],
                "apps": [],
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
            data={"indexers": [], "apps": [], "history": [], "health": []},
            fetched_at=datetime.utcnow(),
        )
        try:
            results = await asyncio.gather(
                self.http_client.get(f"{self.api_base}/indexer"),
                self.http_client.get(f"{self.api_base}/indexerstats"),
                self.http_client.get(f"{self.api_base}/health"),
                self.http_client.get(f"{self.api_base}/application"),
                self.http_client.get(
                    f"{self.api_base}/history",
                    params={"page": 1, "pageSize": 50, "sortKey": "date", "sortDirection": "descending"},
                ),
                return_exceptions=True,
            )

            indexer_resp, stats_resp, health_resp, apps_resp, history_resp = results

            # Indexers with stats
            indexers_raw: list[dict] = []
            if not isinstance(indexer_resp, Exception) and indexer_resp.status_code == 200:
                indexers_raw = indexer_resp.json()

            stats_data: dict = {}
            if not isinstance(stats_resp, Exception) and stats_resp.status_code == 200:
                stats_data = stats_resp.json()

            indexer_stats: dict[int, dict] = {}
            for idx_stat in stats_data.get("indexers", []):
                indexer_stats[idx_stat.get("indexerId", 0)] = idx_stat

            indexers: list[dict] = []
            for idx in indexers_raw:
                idx_id = idx.get("id", 0)
                enabled = idx.get("enable", True)
                stats = indexer_stats.get(idx_id, {})
                queries = stats.get("numberOfQueries", 0)
                grabs = stats.get("numberOfGrabs", 0)
                failures = stats.get("numberOfFailedQueries", 0) + stats.get("numberOfFailedGrabs", 0)

                if not enabled:
                    status = "disabled"
                elif failures > 0 and queries > 0 and failures / max(queries, 1) > 0.5:
                    status = "failing"
                elif failures > 0:
                    status = "degraded"
                else:
                    status = "healthy"

                indexers.append({
                    "id": idx_id,
                    "name": idx.get("name", "Unknown"),
                    "protocol": idx.get("protocol", "unknown"),
                    "privacy": idx.get("privacy", "unknown"),
                    "enabled": enabled,
                    "priority": idx.get("priority", 25),
                    "derived_status": status,
                    "stats": {
                        "number_of_queries": queries,
                        "number_of_grabs": grabs,
                        "number_of_rss_queries": stats.get("numberOfRssQueries", 0),
                        "number_of_failed_queries": stats.get("numberOfFailedQueries", 0),
                        "number_of_failed_grabs": stats.get("numberOfFailedGrabs", 0),
                        "average_response_time_ms": stats.get("averageResponseTime", 0),
                    },
                })

            # Apps
            apps: list[dict] = []
            if not isinstance(apps_resp, Exception) and apps_resp.status_code == 200:
                for app in apps_resp.json():
                    apps.append({
                        "id": app.get("id"),
                        "name": app.get("name", "Unknown"),
                        "implementation": app.get("implementation", ""),
                        "sync_level": app.get("syncLevel", "unknown"),
                        "tags": app.get("tags", []),
                    })

            # History
            history: list[dict] = []
            if not isinstance(history_resp, Exception) and history_resp.status_code == 200:
                for h in history_resp.json().get("records", []):
                    history.append({
                        "id": h.get("id"),
                        "indexer_id": h.get("indexerId"),
                        "event_type": h.get("eventType", ""),
                        "date": h.get("date", ""),
                        "source_title": h.get("sourceTitle", ""),
                        "successful": h.get("successful", True),
                    })

            # Health
            health: list[dict] = []
            if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
                health = health_resp.json()

            return DetailResult(
                data={
                    "indexers": indexers,
                    "apps": apps,
                    "history": history,
                    "health": health,
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning("prowlarr_detail_failed", instance_id=self.instance_id, error=str(e))
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="test_indexer",
                display_name="Test Indexer",
                permission="prowlarr.test",
                category="action",
                params_schema={
                    "properties": {"indexer_id": {"type": "integer", "required": True}}
                },
            ),
            ActionDefinition(
                key="test_all_indexers",
                display_name="Test All Indexers",
                permission="prowlarr.test",
                category="action",
            ),
            ActionDefinition(
                key="sync_apps",
                display_name="Sync with Apps",
                permission="prowlarr.sync",
                category="action",
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        self._ensure_headers()
        try:
            if action == "test_indexer":
                indexer_id = int(params.get("indexer_id", 0))
                if not indexer_id:
                    return ActionResult(success=False, message="No indexer ID provided")
                resp = await self.http_client.post(
                    f"{self.api_base}/indexer/test", json={"id": indexer_id}
                )
                if resp.status_code in (200, 201):
                    return ActionResult(success=True, message="Indexer test passed")
                body = resp.json() if resp.status_code < 500 else {}
                msg = body[0].get("errorMessage", "Test failed") if isinstance(body, list) and body else "Test failed"
                return ActionResult(success=False, message=msg)

            elif action == "test_all_indexers":
                resp = await self.http_client.post(f"{self.api_base}/indexer/testall")
                if resp.status_code in (200, 201):
                    results = resp.json() if resp.status_code == 200 else []
                    failed = [r for r in results if not r.get("isValid", True)]
                    if failed:
                        names = ", ".join(r.get("name", "?") for r in failed[:3])
                        return ActionResult(
                            success=True,
                            message=f"Test complete — {len(failed)} failed: {names}",
                            invalidate_cache=True,
                        )
                    return ActionResult(success=True, message="All indexers passed", invalidate_cache=True)
                return ActionResult(success=False, message=f"Test failed: HTTP {resp.status_code}")

            elif action == "sync_apps":
                return await self._execute_command("AppIndexerSync")

            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")

        except (ValueError, TypeError) as e:
            return ActionResult(success=False, message=f"Invalid parameters: {str(e)}")
        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")
