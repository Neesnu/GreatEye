# Provider: Sonarr

## Overview
The Sonarr provider connects to Sonarr's API (v3) to monitor TV series
libraries, download queues, missing episodes, calendar, and system health.
It supports actions including episode search, manual import, and series
management.

Extends ArrBaseProvider (spec 05) which handles health checks, queue
fetching, command execution, config validation, and authentication.

This provider is expected to have multiple instances (e.g., "Sonarr (HD)"
and "Sonarr (4K)").

## Upstream API
- Base path: `/api/v3/`
- Auth: Inherited from ArrBaseProvider (X-Api-Key header)
- API version: v3 (applies to both Sonarr v3 and v4)
- Documentation: https://sonarr.tv/docs/api/

## Config Schema
Inherited from ArrBaseProvider (url + api_key).
Placeholder URL: `http://10.0.0.45:8989`
Help text for URL: "Full URL to Sonarr web UI (include /sonarr if using URL base)"

## Default Polling Intervals
```json
{
  "health_seconds": 30,
  "summary_seconds": 60,
  "detail_cache_seconds": 300
}
```

## Permissions
| Key                | Display Name              | Category | Notes                              |
|--------------------|---------------------------|----------|------------------------------------|
| sonarr.view        | View Sonarr Data          | read     | Library, queue, calendar, health   |
| sonarr.search      | Search for Episodes       | action   | Trigger automatic episode search   |
| sonarr.import      | Manual Import             | action   | Import downloaded files            |
| sonarr.refresh     | Refresh Series            | action   | Refresh series metadata/disk scan  |
| sonarr.delete      | Delete Series             | admin    | Remove series (+ files optionally) |

## Health Check
Inherited from ArrBaseProvider. Uses `/api/v3/system/status` and
`/api/v3/health`. Validates appName contains "Sonarr".

`_expected_app_name()` returns `"Sonarr"`.

## Summary Data

### Endpoints Used (parallel via asyncio.gather)
```
GET /api/v3/series                              # All series
GET /api/v3/queue?page=1&pageSize=20&includeEpisode=true  # Download queue
GET /api/v3/wanted/missing?page=1&pageSize=1    # Missing count (page=1, size=1 for count only)
GET /api/v3/calendar?start={today}&end={+7days}  # Upcoming episodes
GET /api/v3/health                              # Internal health warnings
```

### Summary Data Shape
```json
{
  "series_count": 150,
  "episode_count": 12500,
  "episode_file_count": 11800,
  "episodes_monitored": 12000,
  "series_monitored": 145,
  "size_on_disk": 2199023255552,
  "size_on_disk_formatted": "2.0 TB",
  "queue": {
    "total": 5,
    "downloading": 3,
    "paused": 0,
    "errors": 1,
    "warnings": 1,
    "records": [
      {
        "series_title": "Show Name",
        "episode_title": "Episode Title",
        "season_number": 3,
        "episode_number": 5,
        "quality": "HDTV-1080p",
        "size": 1073741824,
        "size_formatted": "1.0 GB",
        "sizeleft": 536870912,
        "sizeleft_formatted": "512 MB",
        "progress": 50.0,
        "status": "downloading",
        "timeleft": "00:15:00",
        "error_message": null,
        "download_client": "qBittorrent"
      }
    ]
  },
  "missing_count": 42,
  "calendar_upcoming": [
    {
      "series_title": "Show Name",
      "episode_title": "Episode Title",
      "season_number": 3,
      "episode_number": 6,
      "air_date_utc": "2025-06-20T01:00:00Z",
      "monitored": true,
      "has_file": false
    }
  ],
  "health_warnings": [
    {
      "source": "IndexerRssCheck",
      "type": "warning",
      "message": "No indexers available with RSS sync enabled"
    }
  ]
}
```

### Summary Card Display
The dashboard card should show:
- Series count (total / monitored)
- Queue status with count and any errors (highlighted in red)
- Missing episodes count
- Next upcoming episode (from calendar, with air date)
- Sonarr internal health warnings (if any, amber/red indicator)
- Disk usage (size on disk)
- Mini queue list (top 3-5 active downloads with progress)

### Abstractable Pattern: *arr Summary
The summary shape for Sonarr and Radarr share a common structure:
library counts, queue with error tracking, missing/wanted count, calendar,
and internal health warnings. When building the Radarr provider, this
pattern should be followed with "movies" replacing "series/episodes."

## Detail Data

### Endpoints Used
```
GET /api/v3/series                              # Full series list
GET /api/v3/queue?page=1&pageSize=50&includeEpisode=true&includeSeries=true
GET /api/v3/wanted/missing?page=1&pageSize=20&sortKey=airDateUtc&sortDirection=descending
GET /api/v3/calendar?start={-7days}&end={+30days}&includeSeries=true
GET /api/v3/health
GET /api/v3/diskspace
GET /api/v3/rootfolder
GET /api/v3/qualityprofile
GET /api/v3/tag
```

### Detail Data Shape
```json
{
  "series": [
    {
      "id": 1,
      "title": "Show Name",
      "sort_title": "show name",
      "status": "continuing",
      "overview": "Brief description...",
      "network": "HBO",
      "year": 2020,
      "seasons": [
        {
          "season_number": 1,
          "monitored": true,
          "episode_count": 10,
          "episode_file_count": 10,
          "total_episode_count": 10,
          "percent_of_episodes": 100.0
        }
      ],
      "quality_profile": "HD-1080p",
      "tags": ["important"],
      "size_on_disk": 10737418240,
      "size_on_disk_formatted": "10.0 GB",
      "monitored": true,
      "episode_count": 30,
      "episode_file_count": 28,
      "percent_complete": 93.3,
      "path": "/00_media/TV Shows/Show Name"
    }
  ],
  "queue": {
    "total_records": 5,
    "records": [
      {
        "id": 101,
        "series_id": 1,
        "series_title": "Show Name",
        "episode_id": 501,
        "episode_title": "Episode Title",
        "season_number": 3,
        "episode_number": 5,
        "quality": "HDTV-1080p",
        "custom_formats": ["x264"],
        "size": 1073741824,
        "sizeleft": 536870912,
        "progress": 50.0,
        "status": "downloading",
        "tracked_download_status": "ok",
        "tracked_download_state": "downloading",
        "status_messages": [],
        "error_message": null,
        "timeleft": "00:15:00",
        "download_client": "qBittorrent",
        "indexer": "Prowlarr",
        "output_path": "/00_media/downloads/tv/"
      }
    ]
  },
  "missing": {
    "total_records": 42,
    "records": [
      {
        "series_id": 1,
        "series_title": "Show Name",
        "episode_id": 502,
        "episode_title": "Missing Episode",
        "season_number": 3,
        "episode_number": 4,
        "air_date_utc": "2025-06-10T01:00:00Z",
        "monitored": true
      }
    ]
  },
  "calendar": [
    {
      "series_id": 1,
      "series_title": "Show Name",
      "episode_id": 503,
      "episode_title": "Upcoming Episode",
      "season_number": 3,
      "episode_number": 6,
      "air_date_utc": "2025-06-20T01:00:00Z",
      "monitored": true,
      "has_file": false
    }
  ],
  "health": [],
  "disk_space": [
    {
      "path": "/00_media",
      "label": "Media",
      "free_space": 1099511627776,
      "free_space_formatted": "1.0 TB",
      "total_space": 8796093022208,
      "total_space_formatted": "8.0 TB"
    }
  ],
  "root_folders": [
    {"path": "/00_media/TV Shows/", "free_space": 1099511627776}
  ],
  "quality_profiles": [
    {"id": 1, "name": "HD-1080p"},
    {"id": 2, "name": "Ultra-HD"}
  ],
  "tags": [
    {"id": 1, "label": "important"},
    {"id": 2, "label": "anime"}
  ]
}
```

### Detail View Display
The detail view should show:

**Overview tab:**
- Library stats (series count, episode completion percentage)
- Disk space per root folder with usage bars
- Sonarr health warnings (full list with source and message)
- Quality profile summary

**Queue tab:**
- Full download queue with progress, speed, ETA
- Error/warning indicators per queue item
- Per-item actions: remove from queue, blocklist and search again

**Missing tab:**
- Paginated list of missing episodes (monitored only by default)
- Filter by series
- "Search" action per episode or bulk search
- Sort by air date (most recent missing first)

**Calendar tab:**
- Upcoming episodes for the next 30 days
- Past 7 days with download status (got it / missed it)
- Grouped by date

**Series tab (if needed):**
- Searchable/filterable series list
- Per-series: season breakdown, completion %, monitored status
- Actions: refresh, search all missing, toggle monitoring

## Actions

### search_episode
Trigger an automatic search for a specific episode.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | search_episode                               |
| Permission   | sonarr.search                                |
| Confirm      | false                                        |
| Params       | `{"episode_ids": [501, 502]}`                |

**Endpoint:** `POST /api/v3/command`
```json
{"name": "EpisodeSearch", "episodeIds": [501, 502]}
```

### search_season
Trigger an automatic search for an entire season.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | search_season                                |
| Permission   | sonarr.search                                |
| Confirm      | false                                        |
| Params       | `{"series_id": 1, "season_number": 3}`       |

**Endpoint:** `POST /api/v3/command`
```json
{"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 3}
```

### search_series
Trigger an automatic search for all missing episodes in a series.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | search_series                                |
| Permission   | sonarr.search                                |
| Confirm      | false                                        |
| Params       | `{"series_id": 1}`                           |

**Endpoint:** `POST /api/v3/command`
```json
{"name": "SeriesSearch", "seriesId": 1}
```

### search_missing
Trigger a search for all missing episodes across the library.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | search_missing                               |
| Permission   | sonarr.search                                |
| Confirm      | true                                         |
| Confirm Msg  | "Search for all missing episodes? This may trigger many downloads." |
| Params       | none                                         |

**Endpoint:** `POST /api/v3/command`
```json
{"name": "MissingEpisodeSearch"}
```

### manual_import
Interactive two-phase workflow for importing downloaded files that are stuck
in `importBlocked` state (e.g., "Unable to determine if file is a sample").

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | manual_import                                |
| Permission   | sonarr.import                                |
| Confirm      | false                                        |
| Params       | `{"file_count": 2, "file_path_0": "...", "series_id_0": 2, ...}` |

**UI Flow:**
1. Queue table shows "Import" button for items with `tracked_download_state == "importBlocked"`
2. Clicking Import calls `GET /providers/{id}/manual-import?download_id={downloadId}`
3. This fetches `GET /api/v3/manualimport?downloadId={downloadId}&filterExistingFiles=true`
4. Renders a per-file review table with series/episode/quality/language dropdowns
5. User reviews matches, toggles files on/off, optionally changes series/episode
6. Submitting calls `POST /providers/{id}/manual-import` with form data
7. Provider parses flat form fields into the ManualImport command payload
8. Executes `POST /api/v3/command` with ManualImport command

**Routes (spec 05):**
```
GET  /providers/{id}/manual-import?download_id={downloadId}  — preview UI
POST /providers/{id}/manual-import                           — execute import
GET  /providers/{id}/manual-import/episodes?series_id={id}   — episode dropdown
```

**Episode Dependent Select:** When the user changes the series dropdown,
HTMX fetches episodes for the new series via the episodes endpoint and
replaces the episode `<select>` options.

### refresh_series
Refresh a series — rescan disk and update metadata.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | refresh_series                               |
| Permission   | sonarr.refresh                               |
| Confirm      | false                                        |
| Params       | `{"series_id": 1}`                           |

**Endpoint:** `POST /api/v3/command`
```json
{"name": "RefreshSeries", "seriesId": 1}
```

### delete_series
Delete a series from Sonarr, optionally removing files.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | delete_series                                |
| Permission   | sonarr.delete                                |
| Confirm      | true                                         |
| Confirm Msg  | "Delete '{title}' from Sonarr? This cannot be undone." |
| Params       | `{"series_id": 1, "delete_files": false}`    |

**Endpoint:** `DELETE /api/v3/series/{id}?deleteFiles=false`

If `delete_files` is true, confirmation message adds:
"All episode files will be permanently deleted from disk."

### remove_from_queue
Remove an item from the download queue.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | remove_from_queue                            |
| Permission   | sonarr.search                                |
| Confirm      | false                                        |
| Params       | `{"queue_id": 101, "blocklist": false}`      |

**Endpoint:** `DELETE /api/v3/queue/{id}?removeFromClient=true&blocklist=false`

If `blocklist` is true, the release is added to Sonarr's blocklist
to prevent re-downloading.

### grab_queue_item
Force-grab a queue item, overriding quality/format rejection rules.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | grab_queue_item                              |
| Permission   | sonarr.import                                |
| Confirm      | false                                        |
| Params       | `{"queue_id": 101}`                          |

**Endpoint:** `POST /api/v3/queue/grab/{id}`

Uses shared `ArrBaseProvider._grab_queue_item()` (spec 05).

## Metrics to Track
| Metric                          | Value Source                     | Tags              |
|---------------------------------|----------------------------------|--------------------|
| sonarr.series_count             | series endpoint count            | instance_id        |
| sonarr.episode_count            | aggregated from series           | instance_id        |
| sonarr.episode_file_count       | aggregated from series           | instance_id        |
| sonarr.missing_count            | wanted/missing totalRecords      | instance_id        |
| sonarr.queue_count              | queue totalRecords               | instance_id        |
| sonarr.queue_errors             | queue items with errors          | instance_id        |
| sonarr.size_on_disk             | aggregated from series (bytes)   | instance_id        |
| sonarr.health_warnings          | count from /health               | instance_id        |

## Error Handling

### API Key / Auth Errors
Handled by ArrBaseProvider shared health check and validate_config.

### Large Libraries
Sonarr's `/api/v3/series` returns ALL series in one call. For libraries
with hundreds of series, this can be a large response. The provider should:
- Use this call for both summary and detail (avoid calling it twice)
- Cache aggressively — series data doesn't change every minute
- For summary, compute aggregates (counts, totals) server-side rather
  than sending the full list to the template

### Queue Pagination
Uses ArrBaseProvider._fetch_queue() with Sonarr-specific include params.
Summary fetches page 1 with small page size. Detail fetches more.

### Command Status
Uses ArrBaseProvider._execute_command() fire-and-forget pattern.
UI shows "Search started" rather than "Search complete."

## Sonarr-Specific Implementation Hooks

### _queue_include_params()
```python
def _queue_include_params(self) -> dict:
    return {"includeEpisode": True, "includeSeries": True}
```

### _normalize_queue_record(record)
Maps Sonarr queue records to the standard shape:
- `media_title` → series title
- `detail_title` → "S{season}E{episode} - {episode_title}"

## Validate Config
Inherited from ArrBaseProvider. Verifies appName contains "Sonarr".
