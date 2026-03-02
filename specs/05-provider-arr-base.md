# Provider Base: *arr Family (ArrBaseProvider)

## Overview
Sonarr, Radarr, and Prowlarr share a common API heritage (the *arr family).
They use identical authentication, health check patterns, queue structures,
and command interfaces. ArrBaseProvider is an intermediate abstract class
that sits between BaseProvider and the individual *arr providers, eliminating
duplicated code and ensuring consistent behavior.

```
BaseProvider (spec 03)
  └── ArrBaseProvider (this spec)
        ├── SonarrProvider (spec 06)
        ├── RadarrProvider (spec 07)
        └── ProwlarrProvider (spec 08)
```

## Shared Config Schema
All *arr providers use the same two-field configuration:

```json
{
  "fields": [
    {
      "key": "url",
      "label": "{AppName} URL",
      "type": "url",
      "required": true,
      "placeholder": "http://10.0.0.45:{port}",
      "help_text": "Full URL to {AppName} web UI (include URL base if configured)"
    },
    {
      "key": "api_key",
      "label": "API Key",
      "type": "secret",
      "required": true,
      "help_text": "Found in Settings → General → API Key"
    }
  ]
}
```

Individual providers supply their own placeholder URL and label text,
but the schema shape is identical.

## Shared Authentication
All *arr apps authenticate via a static API key sent as a header:

```python
self.http_client.headers["X-Api-Key"] = config["api_key"]
```

No login flow, no session management, no cookie handling, no token
expiry. The key either works or it doesn't.

## API Version Handling
Most *arr apps use `/api/v3/` but Prowlarr uses `/api/v1/`. The base
class provides a configurable API version:

```python
class ArrBaseProvider(BaseProvider):
    api_version: str = "v3"  # Override in child class if needed

    @property
    def api_base(self) -> str:
        return f"/api/{self.api_version}"
```

All shared methods use `self.api_base` rather than hardcoding a version.
Prowlarr overrides: `api_version = "v1"`.

## Shared Health Check

### Endpoints
```
GET {api_base}/system/status    # App version, name, startup info
GET {api_base}/health           # Internal diagnostics
```

Both calls made in parallel.

### Implementation
```python
async def health_check(self) -> HealthResult:
    """
    Shared health check for all *arr providers.
    Checks system/status for reachability and /health for internal warnings.
    """
    try:
        start = datetime.utcnow()
        status_resp, health_resp = await asyncio.gather(
            self.http_client.get(f"{self.api_base}/system/status"),
            self.http_client.get(f"{self.api_base}/health"),
            return_exceptions=True
        )
        elapsed = (datetime.utcnow() - start).total_seconds() * 1000

        # Handle status response
        if isinstance(status_resp, Exception):
            raise status_resp
        if status_resp.status_code == 401:
            return HealthResult(status=HealthStatus.DOWN, message="Invalid API key")
        if status_resp.status_code != 200:
            return HealthResult(
                status=HealthStatus.DOWN,
                message=f"Unexpected status: {status_resp.status_code}"
            )

        status_data = status_resp.json()
        version = status_data.get("version", "unknown")
        app_name = status_data.get("appName", "unknown")

        # Validate this is the expected app
        if not self._validate_app_name(app_name):
            return HealthResult(
                status=HealthStatus.DOWN,
                message=f"Expected {self._expected_app_name()}, got {app_name}"
            )

        # Process health warnings
        health_issues = []
        if not isinstance(health_resp, Exception) and health_resp.status_code == 200:
            health_issues = health_resp.json()

        errors = [h for h in health_issues if h.get("type") == "error"]
        warnings = [h for h in health_issues if h.get("type") == "warning"]

        # Determine final status
        if elapsed > 3000:
            return HealthResult(
                status=HealthStatus.DEGRADED,
                message=f"Slow response ({elapsed:.0f}ms)",
                response_time_ms=elapsed,
                details={"version": version, "health": health_issues}
            )
        if errors:
            return HealthResult(
                status=HealthStatus.DEGRADED,
                message=f"Connected with {len(errors)} error(s)",
                response_time_ms=elapsed,
                details={"version": version, "health": health_issues}
            )
        if warnings:
            return HealthResult(
                status=HealthStatus.DEGRADED,
                message=f"Connected with {len(warnings)} warning(s)",
                response_time_ms=elapsed,
                details={"version": version, "health": health_issues}
            )

        return HealthResult(
            status=HealthStatus.UP,
            message=f"Connected (v{version})",
            response_time_ms=elapsed,
            details={"version": version}
        )

    except httpx.TimeoutException:
        return HealthResult(status=HealthStatus.DOWN, message="Connection timed out")
    except httpx.ConnectError:
        return HealthResult(status=HealthStatus.DOWN, message="Connection refused")
    except Exception as e:
        return HealthResult(status=HealthStatus.DOWN, message=f"Error: {str(e)}")
```

### App Name Validation
Each child provider implements `_expected_app_name()`:
- SonarrProvider → "Sonarr"
- RadarrProvider → "Radarr"
- ProwlarrProvider → "Prowlarr"

This catches misconfigured URLs (e.g., pointing a Sonarr provider instance
at a Radarr URL).

## Shared Queue Fetching

### Endpoint
```
GET {api_base}/queue?page={page}&pageSize={size}&includeEpisode=true&includeSeries=true
```

Radarr uses the same endpoint with `includeMovie=true` instead.

### Implementation
```python
async def _fetch_queue(self, page: int = 1, page_size: int = 20,
                       include_detail: bool = True) -> dict:
    """
    Fetch download queue with pagination. Shared across *arr providers.
    Returns normalized queue data.
    """
    params = {
        "page": page,
        "pageSize": page_size,
    }
    # Child providers add their own include params
    params.update(self._queue_include_params())

    try:
        response = await self.http_client.get(f"{self.api_base}/queue", params=params)
        if response.status_code != 200:
            return {"total_records": 0, "records": []}

        data = response.json()
        return {
            "total_records": data.get("totalRecords", 0),
            "records": [self._normalize_queue_record(r) for r in data.get("records", [])]
        }
    except Exception:
        return {"total_records": 0, "records": []}
```

Each child provider implements:
- `_queue_include_params()` → returns provider-specific include flags
- `_normalize_queue_record(record)` → normalizes a single queue item
  into the standard shape

### Standard Queue Record Shape
After normalization, all *arr queue records share this structure:

```json
{
  "id": 101,
  "media_title": "Show Name / Movie Title",
  "detail_title": "S03E05 - Episode Title / (2024)",
  "quality": "HDTV-1080p",
  "custom_formats": ["x264"],
  "size": 1073741824,
  "size_formatted": "1.0 GB",
  "sizeleft": 536870912,
  "sizeleft_formatted": "512 MB",
  "progress": 50.0,
  "status": "downloading",
  "tracked_download_status": "ok",
  "tracked_download_state": "downloading",
  "status_messages": [],
  "error_message": null,
  "timeleft": "00:15:00",
  "download_client": "qBittorrent",
  "indexer": "Prowlarr",
  "output_path": "/00_media/downloads/tv/",
  "download_id": "abc123def456"
}
```

The `media_title` and `detail_title` fields are where Sonarr and Radarr
diverge: Sonarr uses series title + episode info, Radarr uses movie title
+ year. The normalization handles this.

## Shared Command Execution

### Endpoint
```
POST {api_base}/command
Content-Type: application/json

{"name": "CommandName", ...params}
```

### Implementation
```python
async def _execute_command(self, command_name: str, **params) -> ActionResult:
    """
    Execute an *arr command. Fire-and-forget pattern.
    Returns success when command is accepted (HTTP 201),
    not when it completes.
    """
    payload = {"name": command_name, **params}

    try:
        response = await self.http_client.post(f"{self.api_base}/command", json=payload)

        if response.status_code in (200, 201):
            return ActionResult(
                success=True,
                message=f"{command_name} started",
                data=response.json(),
                invalidate_cache=True
            )
        else:
            return ActionResult(
                success=False,
                message=f"Command rejected: HTTP {response.status_code}"
            )
    except Exception as e:
        return ActionResult(
            success=False,
            message=f"Command failed: {str(e)}"
        )
```

### Command Status (Informational)
Commands can be polled for status via `GET {api_base}/command/{id}`, which
returns:
```json
{
  "id": 1,
  "name": "EpisodeSearch",
  "commandName": "Episode Search",
  "status": "completed",
  "queued": "2025-06-15T10:30:00Z",
  "started": "2025-06-15T10:30:01Z",
  "ended": "2025-06-15T10:30:05Z",
  "duration": "00:00:04"
}
```

For v1, we do not poll for command completion. Fire-and-forget with cache
invalidation is sufficient. The UI shows "Search started" and the next
summary poll reflects any changes.

## Shared Delete from Queue

### Endpoint
```
DELETE {api_base}/queue/{id}?removeFromClient=true&blocklist=false
```

### Implementation
```python
async def _remove_from_queue(self, queue_id: int,
                              blocklist: bool = False) -> ActionResult:
    """Remove an item from the download queue."""
    try:
        response = await self.http_client.delete(
            f"{self.api_base}/queue/{queue_id}",
            params={"removeFromClient": True, "blocklist": blocklist}
        )
        if response.status_code in (200, 204):
            msg = "Removed from queue"
            if blocklist:
                msg += " and added to blocklist"
            return ActionResult(success=True, message=msg, invalidate_cache=True)
        else:
            return ActionResult(
                success=False,
                message=f"Remove failed: HTTP {response.status_code}"
            )
    except Exception as e:
        return ActionResult(success=False, message=f"Remove failed: {str(e)}")
```

## Shared Grab Queue Item

### Endpoint
```
POST {api_base}/queue/grab/{id}
```

Forces a re-grab of a queue item, overriding any quality/format rejections.
Used when a user wants to manually approve a release that was automatically
rejected by Sonarr/Radarr's quality or custom format rules.

### Implementation
```python
async def _grab_queue_item(self, queue_id: int) -> ActionResult:
    """Grab/re-grab a queue item, overriding rejection rules."""
    try:
        response = await self.http_client.post(
            f"{self.api_base}/queue/grab/{queue_id}"
        )
        if response.status_code in (200, 201):
            return ActionResult(
                success=True,
                message="Release grabbed",
                invalidate_cache=True
            )
        else:
            return ActionResult(
                success=False,
                message=f"Grab failed: HTTP {response.status_code}"
            )
    except Exception as e:
        return ActionResult(success=False, message=f"Grab failed: {str(e)}")
```

## Shared Manual Import

Two-phase interactive workflow for importing downloaded files that are stuck
in `importBlocked` state (e.g., "Unable to determine if file is a sample").

### Preview Endpoint
```
GET {api_base}/manualimport?downloadId={downloadId}&filterExistingFiles=true
```

Returns array of files with auto-detected matches (series/movie, episodes,
quality, languages) and any rejections. The `downloadId` comes from the
queue record's `download_id` field.

### Execute Endpoint
Uses the shared `_execute_command()` with ManualImport command name:
```json
{
  "name": "ManualImport",
  "importMode": "auto",
  "files": [
    {
      "path": "/path/to/file.mkv",
      "seriesId": 2,
      "episodeIds": [501],
      "quality": {"quality": {"id": 3, "name": "HDTV-1080p"}, "revision": {"version": 1}},
      "languages": [{"id": 1, "name": "English"}],
      "releaseGroup": "x264",
      "downloadId": "abc123def456"
    }
  ]
}
```

For Radarr, files use `movieId` instead of `seriesId`/`episodeIds`.

### Implementation
```python
async def _fetch_manual_import_preview(self, download_id: str) -> list[dict]:
    """Fetch file preview for manual import."""

async def _execute_manual_import(self, files: list[dict], import_mode: str = "auto") -> ActionResult:
    """Execute manual import with user-confirmed file list."""
```

Each child provider implements `_normalize_manual_import_file()` to transform
raw preview data into template-friendly dicts.

### Routes
```
GET  /providers/{id}/manual-import?download_id={downloadId}  — preview UI
POST /providers/{id}/manual-import                           — execute import
GET  /providers/{id}/manual-import/episodes?series_id={id}   — Sonarr episode lookup
```

## Shared Validate Config

### Implementation
```python
async def validate_config(self) -> tuple[bool, str]:
    """
    Validate *arr provider configuration.
    Checks connectivity, API key, and that the URL points to the expected app.
    """
    try:
        response = await self.http_client.get(f"{self.api_base}/system/status")

        if response.status_code == 401:
            return False, "Invalid API key"
        if response.status_code == 403:
            return False, "Access denied — check API key permissions"
        if response.status_code != 200:
            return False, f"Unexpected response: HTTP {response.status_code}"

        data = response.json()
        app_name = data.get("appName", "unknown")
        version = data.get("version", "unknown")

        if not self._validate_app_name(app_name):
            return False, f"This appears to be {app_name}, not {self._expected_app_name()}"

        return True, f"Connected to {app_name} v{version}"

    except httpx.ConnectError:
        return False, f"Cannot connect to {self.config['url']}"
    except httpx.TimeoutException:
        return False, f"Connection timed out to {self.config['url']}"
    except Exception as e:
        return False, f"Connection test failed: {str(e)}"
```

## Shared Disk Space Fetching

### Endpoint
```
GET {api_base}/diskspace
```

Returns disk space info for all mounted paths. Identical across all *arr apps.

```python
async def _fetch_disk_space(self) -> list[dict]:
    """Fetch disk space info. Shared across *arr providers."""
    try:
        response = await self.http_client.get(f"{self.api_base}/diskspace")
        if response.status_code != 200:
            return []
        return [
            {
                "path": d.get("path"),
                "label": d.get("label", d.get("path")),
                "free_space": d.get("freeSpace", 0),
                "free_space_formatted": format_bytes(d.get("freeSpace", 0)),
                "total_space": d.get("totalSpace", 0),
                "total_space_formatted": format_bytes(d.get("totalSpace", 0)),
            }
            for d in response.json()
        ]
    except Exception:
        return []
```

## What Child Providers Must Implement

Each *arr provider that extends ArrBaseProvider must implement:

| Method                      | Purpose                                         |
|-----------------------------|--------------------------------------------------|
| `meta()` (static)          | Provider type metadata, permissions               |
| `_expected_app_name()`     | "Sonarr", "Radarr", or "Prowlarr"                |
| `_queue_include_params()`  | Provider-specific query params for queue endpoint |
| `_normalize_queue_record()`| Transform queue record to standard shape          |
| `_normalize_manual_import_file()` | Transform manual import preview file       |
| `get_summary()`            | Provider-specific summary data                    |
| `get_detail()`             | Provider-specific detail data                     |
| `get_actions()`            | Provider-specific action definitions              |
| `execute_action()`         | Provider-specific action dispatch                 |

Methods inherited from ArrBaseProvider (no override needed):
- `health_check()` — shared across all *arr providers
- `validate_config()` — shared across all *arr providers
- `_fetch_queue()` — shared with normalization hook
- `_execute_command()` — shared fire-and-forget
- `_remove_from_queue()` — shared queue removal
- `_grab_queue_item()` — shared queue grab (override rejection)
- `_fetch_manual_import_preview()` — shared manual import file preview
- `_execute_manual_import()` — shared manual import execution
- `_fetch_disk_space()` — shared disk space fetching
