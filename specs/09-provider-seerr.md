# Provider: Seerr

## Overview
The Seerr provider connects to Seerr's API (v1) to monitor media request
activity, approval status, and integration health with Sonarr/Radarr.
Seerr is the request management layer — users submit requests for movies
and TV shows, which are then routed to the *arr apps for acquisition.

Seerr is the unified successor to Overseerr and Jellyseerr, merging both
projects into a single codebase.

**Backward compatibility:** This provider works with both Seerr and
legacy Overseerr instances. The API is identical (`/api/v1/`). During
validate_config, the provider checks for the application name and notes
whether the instance is Seerr or Overseerr, but functionality is the
same. Users still running Overseerr should migrate to Seerr as Overseerr
is no longer receiving updates.

Single instance expected.

## Upstream API
- Base path: `/api/v1/`
- Auth: API key via `X-Api-Key` header (or cookie-based for user context)
- Default port: 5055
- API docs: Available at `http://localhost:5055/api-docs` on each instance
- Documentation: https://docs.seerr.dev/

### Auth Notes
Seerr supports two auth mechanisms:
1. **API key** (X-Api-Key header) — for service-to-service access.
   This is what our provider uses. The key is configured in Seerr under
   Settings → General, or set via the API_KEY environment variable.
2. **Cookie-based user auth** — for user-context operations. Not used
   by our provider.

The API key grants full admin access. All endpoints are available.

## Config Schema
```json
{
  "fields": [
    {
      "key": "url",
      "label": "Seerr URL",
      "type": "url",
      "required": true,
      "placeholder": "http://10.0.0.45:5055",
      "help_text": "Full URL to Seerr (or Overseerr) web UI"
    },
    {
      "key": "api_key",
      "label": "API Key",
      "type": "secret",
      "required": true,
      "help_text": "Found in Settings → General, or set via API_KEY env var"
    }
  ]
}
```

## Default Polling Intervals
```json
{
  "health_seconds": 60,
  "summary_seconds": 120,
  "detail_cache_seconds": 300
}
```

Longer intervals — request activity changes less frequently than
download queues.

## Permissions
| Key                | Display Name              | Category | Notes                              |
|--------------------|---------------------------|----------|------------------------------------|
| seerr.view         | View Seerr Data           | read     | Requests, stats, integration status|
| seerr.approve      | Approve / Decline Requests| action   | Approve or decline pending requests|
| seerr.request      | Submit Requests           | action   | Create new media requests          |

## Health Check

### Endpoint
```
GET /api/v1/status
```

### Logic
1. Send GET with X-Api-Key header
2. Parse response for version and app name
3. Measure response time
4. Also check `/api/v1/settings/main` to verify Sonarr/Radarr integration

### Status Mapping
| Condition                          | Status   | Message                           |
|------------------------------------|----------|-----------------------------------|
| 200, valid response                | UP       | "Connected (v{version})"         |
| 200, response > 3s                | DEGRADED | "Slow response ({time}ms)"       |
| 200, Radarr/Sonarr disconnected   | DEGRADED | "Connected, {service} unreachable"|
| 401/403 (bad API key)             | DOWN     | "Invalid API key"                |
| Connection refused                 | DOWN     | "Connection refused"              |
| Timeout (5s)                       | DOWN     | "Connection timed out"            |

### Integration Health
Seerr connects to Sonarr and Radarr to fulfill requests. If those
connections are broken, Seerr can accept requests but can't send them
downstream. The health check verifies these integrations are working
by checking service connectivity status.

## Summary Data

### Endpoints Used (parallel via asyncio.gather)
```
GET /api/v1/status                                      # App status
GET /api/v1/request?take=20&skip=0&sort=added&filter=all  # Recent requests
GET /api/v1/request/count                               # Request counts by status
GET /api/v1/media?take=1&skip=0&filter=processing       # Processing count
```

### Summary Data Shape
```json
{
  "version": "3.1.0",
  "app_name": "Seerr",
  "is_overseerr": false,
  "request_counts": {
    "pending": 5,
    "approved": 120,
    "processing": 3,
    "available": 800,
    "declined": 2,
    "total": 930
  },
  "recent_requests": [
    {
      "id": 101,
      "type": "movie",
      "media_title": "Movie Name",
      "media_year": 2025,
      "status": "pending",
      "requested_by": "username",
      "requested_at": "2025-06-15T10:30:00Z",
      "media_status": "unknown"
    },
    {
      "id": 102,
      "type": "tv",
      "media_title": "TV Show Name",
      "media_year": 2024,
      "status": "approved",
      "requested_by": "anotheruser",
      "requested_at": "2025-06-14T18:00:00Z",
      "seasons_requested": [3, 4],
      "media_status": "processing"
    }
  ],
  "integration_status": {
    "sonarr": true,
    "radarr": true,
    "plex": true
  }
}
```

### Request Status Mapping
| Seerr Status | Value | Display       | Color  |
|--------------|-------|---------------|--------|
| PENDING      | 1     | Pending       | Amber  |
| APPROVED     | 2     | Approved      | Blue   |
| DECLINED     | 3     | Declined      | Red    |

### Media Status Mapping
| Seerr Status     | Value | Display       | Color  |
|------------------|-------|---------------|--------|
| UNKNOWN          | 1     | Unknown       | Grey   |
| PENDING          | 2     | Pending       | Amber  |
| PROCESSING       | 3     | Processing    | Blue   |
| PARTIALLY_AVAILABLE | 4 | Partial       | Amber  |
| AVAILABLE        | 5     | Available     | Green  |

### Summary Card Display
- Pending requests count (prominent, amber if > 0)
- Recently submitted requests (last 5, with requester and status)
- Processing count (items sent to *arr, awaiting download)
- Integration status indicators (green checks for Sonarr/Radarr/Plex)
- Total request stats (approved, available, declined)

## Detail Data

### Endpoints Used
```
GET /api/v1/request?take=50&skip=0&sort=added&filter=all
GET /api/v1/request/count
GET /api/v1/media?take=20&skip=0&filter=processing
GET /api/v1/media?take=20&skip=0&filter=pending
GET /api/v1/settings/radarr                    # Radarr service configs
GET /api/v1/settings/sonarr                    # Sonarr service configs
```

### Detail Data Shape
```json
{
  "requests": {
    "total_records": 930,
    "records": [
      {
        "id": 101,
        "type": "movie",
        "media": {
          "tmdb_id": 12345,
          "title": "Movie Name",
          "year": 2025,
          "poster_path": "/path/to/poster.jpg",
          "status": "processing"
        },
        "request_status": "approved",
        "requested_by": {
          "id": 5,
          "display_name": "username",
          "avatar": "/url/to/avatar"
        },
        "requested_at": "2025-06-15T10:30:00Z",
        "updated_at": "2025-06-15T11:00:00Z",
        "modified_by": {
          "id": 1,
          "display_name": "admin"
        }
      }
    ]
  },
  "processing_media": [],
  "pending_media": [],
  "services": {
    "radarr": [
      {
        "id": 1,
        "name": "Radarr",
        "is_default": true,
        "is_4k": false,
        "active_directory": "/00_media/Movies/",
        "hostname": "10.0.0.45",
        "port": 7878
      }
    ],
    "sonarr": [
      {
        "id": 1,
        "name": "Sonarr (HD)",
        "is_default": true,
        "is_4k": false,
        "active_directory": "/00_media/TV Shows/",
        "hostname": "10.0.0.45",
        "port": 8989
      }
    ]
  }
}
```

### Detail View Display

**Requests tab:**
- Filterable by status (pending, approved, processing, available, declined)
- Sortable by date, requester, media type
- Per-request: media poster thumbnail, title, requester, status badge
- Pending requests highlighted with approve/decline actions

**Processing tab:**
- Media that has been approved and sent to *arr but not yet available
- Shows which service is handling it (Sonarr/Radarr instance)
- Links conceptually to the queue in the respective *arr provider

**Services tab:**
- Connected Radarr/Sonarr instances with health status
- Shows which instance is default, which is 4K
- Helps diagnose why requests might not be fulfilling

## Actions

### approve_request
Approve a pending media request.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | approve_request                              |
| Permission   | seerr.approve                                |
| Confirm      | false                                        |
| Params       | `{"request_id": 101}`                        |

**Endpoint:** `POST /api/v1/request/{id}/approve`

### decline_request
Decline a pending media request.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | decline_request                              |
| Permission   | seerr.approve                                |
| Confirm      | false                                        |
| Params       | `{"request_id": 101}`                        |

**Endpoint:** `POST /api/v1/request/{id}/decline`

### request_movie
Submit a new movie request.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | request_movie                                |
| Permission   | seerr.request                                |
| Confirm      | false                                        |
| Params       | `{"tmdb_id": 12345}`                         |

**Endpoint:** `POST /api/v1/request`
```json
{"mediaType": "movie", "mediaId": 12345}
```

### request_tv
Submit a new TV show request.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | request_tv                                   |
| Permission   | seerr.request                                |
| Confirm      | false                                        |
| Params       | `{"tmdb_id": 67890, "seasons": [1, 2]}`     |

**Endpoint:** `POST /api/v1/request`
```json
{"mediaType": "tv", "mediaId": 67890, "seasons": [1, 2]}
```

## Metrics to Track
| Metric                          | Value Source                     | Tags              |
|---------------------------------|----------------------------------|--------------------|
| seerr.pending_requests          | request/count pending            | instance_id        |
| seerr.approved_requests         | request/count approved           | instance_id        |
| seerr.processing_media          | media processing count           | instance_id        |
| seerr.available_media           | request/count available          | instance_id        |
| seerr.total_requests            | request/count total              | instance_id        |

Trending pending requests reveals if auto-approve is working or if
requests are piling up without admin attention.

## Error Handling

### Overseerr Compatibility
The provider detects whether it's talking to Seerr or Overseerr by
checking the response from `/api/v1/status`. Both return a version
string and status info. The provider logs which app is detected but
operates identically. If an Overseerr-specific API difference is
encountered in the future, the provider can branch on the detected
app name.

### Request Failures
- Requesting media that already exists returns a specific error message.
  The provider should surface this as a user-friendly message rather
  than a generic failure.
- Requesting media when Radarr/Sonarr is disconnected will fail. The
  health check should already show DEGRADED in this case.

### Rate Limiting
Seerr's CSRF protection (if enabled) blocks external API calls that
modify data. The provider uses the API key header which bypasses CSRF.
However, if the admin has enabled CSRF and it somehow affects API key
access, the provider should detect and report this clearly.

## Validate Config
```python
async def validate_config(self) -> tuple[bool, str]:
    try:
        response = await self.http_client.get("/api/v1/status")
        if response.status_code == 401:
            return False, "Invalid API key"
        if response.status_code != 200:
            return False, f"Unexpected response: HTTP {response.status_code}"

        data = response.json()
        version = data.get("version", "unknown")

        # Detect Seerr vs Overseerr
        app_name = "Seerr"  # Default assumption
        if "overseerr" in str(data).lower():
            app_name = "Overseerr (consider migrating to Seerr)"

        return True, f"Connected to {app_name} v{version}"

    except httpx.ConnectError:
        return False, f"Cannot connect to {self.config['url']}"
    except Exception as e:
        return False, f"Connection test failed: {str(e)}"
```
