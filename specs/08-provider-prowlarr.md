# Provider: Prowlarr

## Overview
The Prowlarr provider connects to Prowlarr's API (v1) to monitor indexer
health, search statistics, and sync status with downstream *arr apps.
Prowlarr is the indexer manager — it doesn't have a media library, queue,
or calendar like Sonarr/Radarr. Its value in the dashboard is surfacing
indexer issues before they impact downloads.

Extends ArrBaseProvider (spec 05) for shared health check and config
validation patterns.

Single instance expected (one Prowlarr managing all indexers).

## Upstream API
- Base path: `/api/v1/` (note: v1, not v3 like Sonarr/Radarr)
- Auth: Inherited from ArrBaseProvider (X-Api-Key header)
- Documentation: https://prowlarr.com/docs/api/

### API Version Note
Prowlarr uses API v1, not v3. The ArrBaseProvider shared methods that
reference `/api/v3/` paths need to be overridden or made version-aware.
The simplest approach: Prowlarr overrides the API base path.

```python
# In ProwlarrProvider.__init__
self.api_base = "/api/v1"
```

The ArrBaseProvider health check, validate_config, and other shared
methods should use `self.api_base` rather than hardcoding `/api/v3/`.

## Config Schema
Inherited from ArrBaseProvider (url + api_key).
Placeholder URL: `http://10.0.0.45:9696`
Help text for URL: "Full URL to Prowlarr web UI"

## Default Polling Intervals
```json
{
  "health_seconds": 60,
  "summary_seconds": 120,
  "detail_cache_seconds": 300
}
```

Longer intervals than Sonarr/Radarr — indexer status changes less
frequently than download queues.

## Permissions
| Key                  | Display Name              | Category | Notes                          |
|----------------------|---------------------------|----------|--------------------------------|
| prowlarr.view        | View Prowlarr Data        | read     | Indexer status and stats       |
| prowlarr.search      | Search Indexers           | action   | Trigger a manual search        |
| prowlarr.test        | Test Indexers             | action   | Test indexer connectivity      |
| prowlarr.sync        | Sync with Apps            | action   | Trigger app sync               |

## Health Check
Inherited from ArrBaseProvider with API path override to `/api/v1/`.
Uses `/api/v1/system/status` and `/api/v1/health`.

`_expected_app_name()` returns `"Prowlarr"`.

Prowlarr's `/health` endpoint surfaces indexer-specific issues:
- Indexers returning errors
- Indexers with expired VIP/API access
- Download clients unreachable from Prowlarr
- App sync failures (can't push indexers to Sonarr/Radarr)

## Summary Data

### Endpoints Used (parallel via asyncio.gather)
```
GET /api/v1/indexer                  # All configured indexers
GET /api/v1/indexerstats             # Search/grab statistics per indexer
GET /api/v1/health                   # Internal health warnings
GET /api/v1/application              # Configured downstream apps (Sonarr, Radarr)
```

### Summary Data Shape
```json
{
  "indexer_count": 12,
  "indexers_healthy": 10,
  "indexers_failing": 1,
  "indexers_disabled": 1,
  "total_grabs_today": 15,
  "total_queries_today": 250,
  "indexers": [
    {
      "id": 1,
      "name": "IndexerName",
      "protocol": "torrent",
      "enabled": true,
      "status": "healthy",
      "average_response_time_ms": 350,
      "number_of_grabs": 5,
      "number_of_queries": 80,
      "number_of_failures": 0,
      "last_failure": null
    }
  ],
  "apps": [
    {
      "id": 1,
      "name": "Sonarr",
      "sync_level": "fullSync",
      "sync_status": "ok"
    },
    {
      "id": 2,
      "name": "Radarr",
      "sync_level": "fullSync",
      "sync_status": "ok"
    }
  ],
  "health_warnings": []
}
```

### Summary Card Display
- Indexer health bar: {healthy} / {total} indexers healthy
- Failing indexers count (red if > 0, with names)
- Today's stats: total queries and grabs
- App sync status (green checks for each connected app)
- Health warnings from Prowlarr internals

### Indexer Status Derivation
Prowlarr doesn't have a single "status" field per indexer. Status is
derived from indexer stats and the health endpoint:

| Condition                                  | Derived Status |
|--------------------------------------------|----------------|
| Enabled, no recent failures                | healthy        |
| Enabled, some failures but still working   | degraded       |
| Enabled, all recent queries failing        | failing        |
| Disabled by user                           | disabled       |
| Disabled by Prowlarr (too many failures)   | auto_disabled  |

## Detail Data

### Endpoints Used
```
GET /api/v1/indexer                  # Full indexer configs
GET /api/v1/indexerstats             # Detailed stats per indexer
GET /api/v1/health
GET /api/v1/application              # App sync details
GET /api/v1/history?page=1&pageSize=50&sortKey=date&sortDirection=descending
```

### Detail Data Shape
```json
{
  "indexers": [
    {
      "id": 1,
      "name": "IndexerName",
      "protocol": "torrent",
      "privacy": "private",
      "enabled": true,
      "priority": 25,
      "app_profile_id": 1,
      "fields": {
        "baseUrl": "https://indexer.example.com",
        "categories": [2000, 5000]
      },
      "stats": {
        "number_of_queries": 500,
        "number_of_grabs": 25,
        "number_of_rss_queries": 200,
        "number_of_auth_queries": 0,
        "number_of_failed_queries": 3,
        "number_of_failed_grabs": 0,
        "average_response_time_ms": 350
      },
      "derived_status": "healthy"
    }
  ],
  "apps": [
    {
      "id": 1,
      "name": "Sonarr",
      "implementation": "Sonarr",
      "sync_level": "fullSync",
      "base_url": "http://10.0.0.45:8989",
      "tags": []
    }
  ],
  "history": [
    {
      "id": 101,
      "indexer_id": 1,
      "indexer_name": "IndexerName",
      "event_type": "releaseGrabbed",
      "date": "2025-06-15T10:30:00Z",
      "source_title": "Release.Name.S03E05.1080p",
      "successful": true,
      "download_client": "qBittorrent"
    }
  ],
  "health": []
}
```

### Detail View Display

**Indexers tab:**
- Table of all indexers with status indicators
- Per-indexer: query count, grab count, failure count, avg response time
- Sort by name, status, or activity
- "Test" action per indexer
- Color coding: green (healthy), amber (degraded), red (failing), grey (disabled)

**Apps tab:**
- Connected downstream applications (Sonarr, Radarr, etc.)
- Sync status per app
- "Sync" action to push indexer config

**History tab:**
- Recent search/grab activity
- Filter by indexer or event type
- Shows which releases were grabbed and from where

## Actions

### test_indexer
Test connectivity to a specific indexer.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | test_indexer                                 |
| Permission   | prowlarr.test                                |
| Confirm      | false                                        |
| Params       | `{"indexer_id": 1}`                          |

**Endpoint:** `POST /api/v1/indexer/test`
```json
{"id": 1}
```

Returns success/failure with error message if the indexer can't be reached.

### test_all_indexers
Test all enabled indexers.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | test_all_indexers                             |
| Permission   | prowlarr.test                                |
| Confirm      | false                                        |
| Params       | none                                         |

**Endpoint:** `POST /api/v1/indexer/testall`

### search
Execute a manual search across indexers.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | search                                       |
| Permission   | prowlarr.search                              |
| Confirm      | false                                        |
| Params       | `{"query": "search term", "type": "search", "categories": [2000]}` |

**Endpoint:** `GET /api/v1/search?query={query}&type=search&categories={cats}`

Note: Unlike Sonarr/Radarr commands, Prowlarr search is a synchronous
GET that returns results directly. The provider should return the result
count and top results rather than fire-and-forget.

### sync_apps
Trigger a sync of indexer configuration to downstream apps.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | sync_apps                                    |
| Permission   | prowlarr.sync                                |
| Confirm      | false                                        |
| Params       | none                                         |

**Endpoint:** `POST /api/v1/command`
```json
{"name": "AppIndexerSync"}
```

## Metrics to Track
| Metric                           | Value Source                    | Tags              |
|----------------------------------|--------------------------------|--------------------|
| prowlarr.indexer_count           | indexer endpoint count          | instance_id        |
| prowlarr.indexers_healthy        | derived status count           | instance_id        |
| prowlarr.indexers_failing        | derived status count           | instance_id        |
| prowlarr.total_queries           | indexerstats aggregated         | instance_id        |
| prowlarr.total_grabs             | indexerstats aggregated         | instance_id        |
| prowlarr.total_failures          | indexerstats aggregated         | instance_id        |
| prowlarr.health_warnings         | count from /health             | instance_id        |

Trending failures over time reveals which indexers are becoming unreliable.

## Key Differences from Sonarr/Radarr
| Aspect              | Sonarr/Radarr                  | Prowlarr                        |
|---------------------|--------------------------------|---------------------------------|
| API version         | v3                             | v1                              |
| Has media library   | Yes                            | No                              |
| Has download queue  | Yes                            | No                              |
| Has calendar        | Yes                            | No                              |
| Has wanted/missing  | Yes                            | No                              |
| Primary concern     | Media completion               | Indexer availability             |
| Search              | Async command                  | Sync GET with results           |
| Shared methods used | health, queue, commands, disk  | health, validate_config only    |

Prowlarr uses fewer ArrBaseProvider shared methods than Sonarr/Radarr
since it has no queue or media-level commands. It primarily benefits
from the shared health check, config validation, and API key auth.

## ArrBaseProvider API Path Override
Since Prowlarr uses `/api/v1/` instead of `/api/v3/`, the ArrBaseProvider
needs to support configurable API base paths. Implementation approach:

```python
class ArrBaseProvider(BaseProvider):
    api_version: str = "v3"  # Default, overridden by Prowlarr

    @property
    def api_base(self) -> str:
        return f"/api/{self.api_version}"
```

```python
class ProwlarrProvider(ArrBaseProvider):
    api_version = "v1"
```

All shared methods use `self.api_base` prefix for endpoint paths.

## Validate Config
Inherited from ArrBaseProvider (with v1 path). Verifies appName contains
"Prowlarr".
