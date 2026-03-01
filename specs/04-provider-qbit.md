# Provider: qBittorrent

## Overview
The qBittorrent provider connects to qBittorrent's Web API (v2.x) to monitor
download activity, transfer speeds, and queue status. It supports actions like
pause, resume, and delete for individual torrents.

This provider is expected to have multiple instances (e.g., "qBittorrent" and
"qBittorrent (Old)").

## Upstream API
- Base path: `/api/v2/`
- Auth: Cookie-based (POST `/api/v2/auth/login` with username/password)
- Minimum supported version: qBittorrent v4.1+ (Web API v2.0+)
- Current target version: qBittorrent v5.1.4 (Web API v2.11.4)
- Documentation: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)

### Authentication Notes
qBittorrent uses cookie-based auth. The provider must:
1. POST to `/api/v2/auth/login` with username and password
2. Store the returned SID cookie
3. Include the cookie on all subsequent requests
4. Re-authenticate if a request returns 403 (cookie expired)
5. Handle the case where auth is disabled (no credentials needed)

## Config Schema
```json
{
  "fields": [
    {
      "key": "url",
      "label": "qBittorrent URL",
      "type": "url",
      "required": true,
      "placeholder": "http://10.0.0.45:8080",
      "help_text": "Full URL to qBittorrent Web UI"
    },
    {
      "key": "username",
      "label": "Username",
      "type": "string",
      "required": false,
      "help_text": "Leave blank if authentication is disabled"
    },
    {
      "key": "password",
      "label": "Password",
      "type": "secret",
      "required": false
    },
    {
      "key": "recent_limit",
      "label": "Recent Transfers Limit",
      "type": "integer",
      "required": false,
      "default": 30,
      "min": 10,
      "max": 100,
      "help_text": "Maximum number of recent transfers to display"
    }
  ]
}
```

## Default Polling Intervals
```json
{
  "health_seconds": 30,
  "summary_seconds": 60,
  "detail_cache_seconds": 30
}
```

Detail cache TTL is shorter than other providers because download state
changes rapidly (progress, speeds).

## Permissions
| Key                   | Display Name            | Category | Notes                         |
|-----------------------|-------------------------|----------|-------------------------------|
| qbittorrent.view      | View Downloads          | read     | See transfer list and stats   |
| qbittorrent.pause     | Pause / Resume Torrents | action   | Pause or resume transfers     |
| qbittorrent.delete    | Delete Torrents         | admin    | Remove torrents (+ files opt) |
| qbittorrent.speed     | Set Speed Limits        | action   | Toggle alt speed, set limits  |

## Health Check

### Endpoint
```
GET /api/v2/app/version
```

### Logic
1. Attempt authentication (if credentials configured)
2. GET `/api/v2/app/version` — returns the qBittorrent version string
3. Measure response time

### Status Mapping
| Condition                          | Status   | Message                          |
|------------------------------------|----------|----------------------------------|
| Response 200, version returned     | UP       | "Connected (v{version})"        |
| Response 200, response > 3s       | DEGRADED | "Slow response ({time}ms)"      |
| Response 403 (auth failed)         | DOWN     | "Authentication failed"          |
| Connection refused                 | DOWN     | "Connection refused"             |
| Timeout (5s)                       | DOWN     | "Connection timed out"           |
| Other error                        | DOWN     | Error description                |

## Summary Data

### Endpoints Used
```
GET /api/v2/transfer/info
GET /api/v2/torrents/info?sort=added_on&reverse=true&limit={recent_limit}
```

Both calls are made in parallel (asyncio.gather) to minimize poll time.

### Summary Data Shape
```json
{
  "global_download_speed": 15728640,
  "global_upload_speed": 5242880,
  "global_download_speed_formatted": "15.0 MB/s",
  "global_upload_speed_formatted": "5.0 MB/s",
  "active_downloads": 3,
  "active_uploads": 12,
  "paused": 2,
  "errored": 0,
  "total_torrents": 156,
  "free_disk_space": 1099511627776,
  "free_disk_space_formatted": "1.0 TB",
  "alt_speed_enabled": false,
  "connection_status": "connected",
  "recent_torrents": [
    {
      "hash": "abc123...",
      "name": "Example.Torrent.Name",
      "state": "downloading",
      "progress": 0.45,
      "size": 4294967296,
      "size_formatted": "4.0 GB",
      "download_speed": 5242880,
      "download_speed_formatted": "5.0 MB/s",
      "upload_speed": 1048576,
      "upload_speed_formatted": "1.0 MB/s",
      "eta": 600,
      "eta_formatted": "10m",
      "category": "movies",
      "tags": "radarr",
      "added_on": "2025-06-15T10:30:00Z",
      "ratio": 1.5,
      "tracker": "tracker.example.com"
    }
  ]
}
```

### Summary Card Display
The dashboard card should show:
- Download / upload speed (large, prominent)
- Active downloads / uploads count
- Errored torrent count (highlighted if > 0)
- Alt speed toggle indicator
- Mini list of top ~5 active transfers with progress bars
- Free disk space

### Transfer State Mapping
qBittorrent returns torrent states as strings. Map them to display categories:

| qBit State              | Display Category | Icon Color |
|-------------------------|------------------|------------|
| downloading             | Downloading      | Blue       |
| stalledDL               | Stalled (DL)     | Amber      |
| uploading               | Seeding          | Green      |
| stalledUP               | Seeding (idle)   | Green dim  |
| pausedDL                | Paused           | Grey       |
| pausedUP                | Paused           | Grey       |
| queuedDL                | Queued           | Grey       |
| queuedUP                | Queued           | Grey       |
| checkingDL              | Checking         | Amber      |
| checkingUP              | Checking         | Amber      |
| forcedDL                | Forced Download  | Blue       |
| forcedUP                | Forced Upload    | Green      |
| error                   | Error            | Red        |
| missingFiles            | Error            | Red        |
| moving                  | Moving           | Amber      |
| unknown                 | Unknown          | Grey       |

Note: qBittorrent v5.x renamed "paused" states to "stopped" variants
(stoppedDL, stoppedUP). The provider should handle both naming conventions
for backward compatibility.

## Detail Data

### Endpoints Used
```
GET /api/v2/torrents/info?sort=added_on&reverse=true&limit={recent_limit}
GET /api/v2/transfer/info
GET /api/v2/sync/maindata
```

### Detail Data Shape
```json
{
  "transfer": {
    "download_speed": 15728640,
    "upload_speed": 5242880,
    "download_session": 53687091200,
    "upload_session": 21474836480,
    "download_total": 1099511627776,
    "upload_total": 549755813888,
    "free_disk_space": 1099511627776,
    "dht_nodes": 450,
    "connection_status": "connected",
    "alt_speed_enabled": false,
    "download_limit": 0,
    "upload_limit": 0
  },
  "torrents": [
    {
      "hash": "abc123...",
      "name": "Example.Torrent.Name",
      "state": "downloading",
      "progress": 0.45,
      "size": 4294967296,
      "downloaded": 1932735283,
      "uploaded": 966367641,
      "download_speed": 5242880,
      "upload_speed": 1048576,
      "eta": 600,
      "ratio": 0.5,
      "category": "movies",
      "tags": "radarr",
      "added_on": "2025-06-15T10:30:00Z",
      "completion_on": null,
      "tracker": "tracker.example.com",
      "num_seeds": 15,
      "num_leeches": 3,
      "save_path": "/00_media/downloads/movies/",
      "content_path": "/00_media/downloads/movies/Example.Torrent.Name"
    }
  ],
  "categories": {
    "movies": {"name": "movies", "savePath": "/00_media/downloads/movies/"},
    "tv": {"name": "tv", "savePath": "/00_media/downloads/tv/"}
  },
  "tags": ["radarr", "sonarr", "cross-seed"]
}
```

### Detail View Display
The detail view should show:
- Full transfer statistics (session totals, global speeds, DHT nodes)
- Speed limit controls (alt speed toggle, manual limits)
- Sortable/filterable torrent list with all fields
- Torrent state grouping (tabs or filters for downloading/seeding/paused/errored)
- Category and tag filtering
- Per-torrent actions (pause, resume, delete)
- Progress bars with percentage and ETA
- Ratio displayed per torrent

## Actions

### pause
Pause one or more torrents.

| Field        | Value                          |
|--------------|--------------------------------|
| Key          | pause                          |
| Permission   | qbittorrent.pause              |
| Confirm      | false                          |
| Params       | `{"hashes": ["abc...", "def..."]}` or `{"hashes": ["all"]}` |

**Endpoint:** `POST /api/v2/torrents/pause` with form data `hashes=abc...|def...`

**Result:** Returns success if HTTP 200. qBit returns no body on success.

### resume
Resume one or more paused torrents.

| Field        | Value                          |
|--------------|--------------------------------|
| Key          | resume                         |
| Permission   | qbittorrent.pause              |
| Confirm      | false                          |
| Params       | `{"hashes": ["abc...", "def..."]}` or `{"hashes": ["all"]}` |

**Endpoint:** `POST /api/v2/torrents/resume` with form data `hashes=abc...|def...`

### delete
Delete one or more torrents, optionally deleting files.

| Field        | Value                          |
|--------------|--------------------------------|
| Key          | delete                         |
| Permission   | qbittorrent.delete             |
| Confirm      | true                           |
| Confirm Msg  | "Delete {count} torrent(s)? This cannot be undone." |
| Params       | `{"hashes": ["abc..."], "delete_files": false}` |

**Endpoint:** `POST /api/v2/torrents/delete` with form data
`hashes=abc...|def...&deleteFiles=false`

If `delete_files` is true, the confirmation message should include
"Files will be permanently deleted from disk."

### toggle_alt_speed
Toggle alternative speed limits on/off.

| Field        | Value                          |
|--------------|--------------------------------|
| Key          | toggle_alt_speed               |
| Permission   | qbittorrent.speed              |
| Confirm      | false                          |
| Params       | none                           |

**Endpoint:** `POST /api/v2/transfer/toggleSpeedLimitsMode`

### set_download_limit
Set global download speed limit.

| Field        | Value                          |
|--------------|--------------------------------|
| Key          | set_download_limit             |
| Permission   | qbittorrent.speed              |
| Confirm      | false                          |
| Params       | `{"limit": 10485760}` (bytes/s, 0 = unlimited) |

**Endpoint:** `POST /api/v2/transfer/setDownloadLimit` with form data `limit=10485760`

### set_upload_limit
Set global upload speed limit.

| Field        | Value                          |
|--------------|--------------------------------|
| Key          | set_upload_limit               |
| Permission   | qbittorrent.speed              |
| Confirm      | false                          |
| Params       | `{"limit": 5242880}` (bytes/s, 0 = unlimited) |

**Endpoint:** `POST /api/v2/transfer/setUploadLimit` with form data `limit=5242880`

## Metrics to Track
The following metrics are written to MetricsStore on each summary poll:

| Metric                           | Value Source                   | Tags              |
|----------------------------------|--------------------------------|--------------------|
| qbittorrent.download_speed       | transfer/info dl_info_speed    | instance_id        |
| qbittorrent.upload_speed         | transfer/info up_info_speed    | instance_id        |
| qbittorrent.active_downloads     | Count of downloading torrents  | instance_id        |
| qbittorrent.active_uploads       | Count of uploading torrents    | instance_id        |
| qbittorrent.total_torrents       | Total torrent count            | instance_id        |
| qbittorrent.errored_torrents     | Count of errored torrents      | instance_id        |
| qbittorrent.free_disk_space      | transfer/info free_space       | instance_id        |

These metrics enable historical views: "What did my download speed look like
over the last week?" and alert-worthy trends: "Errored torrent count has been
rising."

## Error Handling

### Authentication Expiry
qBittorrent session cookies expire. The provider must:
1. Detect 403 responses on any API call
2. Re-authenticate by calling `/api/v2/auth/login`
3. Retry the failed request once with the new cookie
4. If re-auth fails, mark health as DOWN with "Authentication failed"

### Rate Limiting
qBittorrent has built-in rate limiting (configurable via `web_ui_max_auth_fail_count`
and `web_ui_ban_duration`). The provider should:
- Never retry authentication more than once per poll cycle
- Back off if receiving 403 repeatedly (may be IP-banned)
- Log a clear warning if IP ban is suspected

### Large Torrent Lists
If a qBit instance has thousands of torrents, `/torrents/info` without a
limit could be slow. The provider always passes `limit={recent_limit}` to
cap the response. The detail view uses the same limit. Total torrent count
comes from `/sync/maindata` which is lightweight.

### Version Compatibility
The provider should handle both v4.x and v5.x naming conventions:
- v4.x uses `pausedDL`/`pausedUP` for states
- v5.x uses `stoppedDL`/`stoppedUP` for the same states
- v4.x uses `/torrents/pause` and `/torrents/resume`
- v5.x uses `/torrents/stop` and `/torrents/start`

The provider checks the qBit version on first health check and stores
it for the lifetime of the instance. API calls use the appropriate
endpoint names based on the detected version.

## Validate Config
The `validate_config()` implementation should:
1. Attempt auth (if credentials provided)
2. Call `/api/v2/app/version`
3. Return success with version string, or specific failure reason:
   - "Cannot connect to {url}" — connection refused
   - "Authentication failed — check username and password"
   - "Connected but unexpected response — is this a qBittorrent instance?"

## Formatting Utilities
The provider uses shared formatting utilities (defined in a common module):

- `format_bytes(bytes)` → "4.0 GB", "15.0 MB/s"
- `format_speed(bytes_per_sec)` → "15.0 MB/s"
- `format_eta(seconds)` → "10m", "2h 30m", "1d 5h", "∞"
- `format_timestamp(unix_ts)` → ISO 8601 datetime string

These formatted values are included alongside raw values in the data shape
so templates can use them directly without client-side formatting.
