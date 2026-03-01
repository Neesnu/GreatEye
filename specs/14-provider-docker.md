# Provider: Docker

## Overview
The Docker provider connects to the Docker Engine API via the Unix
socket to monitor container status, resource usage, and provide basic
container lifecycle actions. This is the only provider that interacts
with the host system rather than a remote service.

This provider gives a bird's-eye view of all containers on the Unraid
host, complementing the individual service providers. When a service
goes DOWN, the Docker provider can show whether the container itself
is running or crashed.

Single instance (one Docker daemon per host).

## Upstream API
- Socket: `/var/run/docker.sock` (Unix socket, mounted into container)
- API version: v1.41+ (Docker Engine API)
- Auth: None (socket access implies trust)
- Response format: JSON
- Documentation: https://docs.docker.com/engine/api/

### Access Notes
The Docker socket is mounted read-only into the Great Eye container:
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

The `:ro` mount limits what operations are possible. Container
inspection and stats work. Container restart requires read-write
mount — this is configurable via the Unraid template.

```python
# Using httpx with Unix socket transport
import httpx

transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
client = httpx.AsyncClient(transport=transport, base_url="http://docker")
```

## Config Schema
```json
{
  "fields": [
    {
      "key": "socket_path",
      "label": "Docker Socket Path",
      "type": "string",
      "required": true,
      "default": "/var/run/docker.sock",
      "help_text": "Path to Docker Unix socket"
    },
    {
      "key": "show_all",
      "label": "Show Stopped Containers",
      "type": "boolean",
      "required": false,
      "default": true,
      "help_text": "Include stopped/exited containers in the list"
    }
  ]
}
```

No secrets needed — socket access is the auth mechanism.

## Default Polling Intervals
```json
{
  "health_seconds": 30,
  "summary_seconds": 30,
  "detail_cache_seconds": 60
}
```

Short intervals — container status changes are operationally important.

## Permissions
| Key                  | Display Name              | Category | Notes                          |
|----------------------|---------------------------|----------|--------------------------------|
| docker.view          | View Docker Data          | read     | Container list, stats          |
| docker.restart       | Restart Containers        | action   | Restart a running container    |
| docker.startstop     | Start/Stop Containers     | admin    | Start or stop containers       |

## Health Check

### Endpoint
```
GET /version
```

Returns Docker Engine version info. If the socket is accessible and
Docker daemon is running, this succeeds.

### Status Mapping
| Condition                          | Status   | Message                           |
|------------------------------------|----------|-----------------------------------|
| 200, daemon responding             | UP       | "Docker {version}"               |
| Socket not found                   | DOWN     | "Docker socket not mounted"      |
| Permission denied                  | DOWN     | "Socket permission denied"       |
| Timeout                            | DOWN     | "Docker daemon not responding"   |

## Summary Data

### Endpoints Used
```
GET /containers/json?all={show_all}     # Container list
GET /info                                # Docker system info
```

### Summary Data Shape
```json
{
  "docker_version": "24.0.7",
  "containers": {
    "total": 25,
    "running": 22,
    "stopped": 2,
    "unhealthy": 1
  },
  "container_list": [
    {
      "id": "abc123def456",
      "name": "sonarr",
      "image": "lscr.io/linuxserver/sonarr:latest",
      "state": "running",
      "status": "Up 5 days",
      "health": "healthy",
      "created": "2025-06-10T08:00:00Z",
      "ports": [
        {"private": 8989, "public": 8989, "type": "tcp"}
      ],
      "mounts": 3
    }
  ],
  "system": {
    "images": 30,
    "memory_total": 68719476736,
    "memory_total_formatted": "64.0 GB",
    "cpus": 16,
    "os": "linux",
    "kernel": "5.15.0-unraid"
  }
}
```

### Container State Mapping
| Docker State | Display   | Color  |
|--------------|-----------|--------|
| running      | Running   | Green  |
| created      | Created   | Grey   |
| restarting   | Restarting| Amber  |
| paused       | Paused    | Amber  |
| exited       | Stopped   | Grey   |
| dead         | Dead      | Red    |

### Health Check Status (if container has HEALTHCHECK)
| Docker Health | Display     | Color  |
|---------------|-------------|--------|
| healthy       | Healthy     | Green  |
| unhealthy     | Unhealthy   | Red    |
| starting      | Starting    | Amber  |
| none          | (no check)  | Grey   |

### Summary Card Display
- Container count: running / total
- Unhealthy containers highlighted (red count if > 0)
- Mini container list with state indicators
- Host info: CPU count, total memory, kernel version

## Detail Data

### Endpoints Used
```
GET /containers/json?all=true
GET /containers/{id}/stats?stream=false   # Per-container resource usage
GET /containers/{id}/json                 # Full container inspection
GET /info
GET /version
```

### Per-Container Stats Shape (from /stats)
```json
{
  "cpu_percent": 2.5,
  "memory_usage": 536870912,
  "memory_usage_formatted": "512 MB",
  "memory_limit": 68719476736,
  "memory_percent": 0.78,
  "network_rx": 1073741824,
  "network_rx_formatted": "1.0 GB",
  "network_tx": 536870912,
  "network_tx_formatted": "512 MB",
  "block_read": 10737418240,
  "block_write": 5368709120
}
```

Note: `/containers/{id}/stats?stream=false` returns a single snapshot.
Computing CPU percentage requires comparing with the previous reading
(delta calculation), which the provider handles internally.

### Detail View Display

**Containers tab:**
- Full container list with state, health, uptime
- Sort by name, state, CPU, memory
- Filter: running only, all, unhealthy only
- Per-container: name, image, state, ports, uptime, resource usage
- Actions: restart, start, stop

**Resources tab:**
- Per-container CPU and memory usage
- Sorted by resource consumption (find the heavy hitters)
- System totals: available vs used

**System tab:**
- Docker engine version, storage driver
- Host OS, kernel version, architecture
- Total images, volumes, networks

## Actions

### restart_container
Restart a running container.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | restart_container                            |
| Permission   | docker.restart                               |
| Confirm      | true                                         |
| Confirm Msg  | "Restart container '{name}'?"                |
| Params       | `{"container_id": "abc123def456"}`           |

**Endpoint:** `POST /containers/{id}/restart`

### stop_container
Stop a running container.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | stop_container                               |
| Permission   | docker.startstop                             |
| Confirm      | true                                         |
| Confirm Msg  | "Stop container '{name}'?"                   |
| Params       | `{"container_id": "abc123def456"}`           |

**Endpoint:** `POST /containers/{id}/stop`

### start_container
Start a stopped container.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | start_container                              |
| Permission   | docker.startstop                             |
| Confirm      | false                                        |
| Params       | `{"container_id": "abc123def456"}`           |

**Endpoint:** `POST /containers/{id}/start`

## Safety Guardrails

### No Image Operations
The Docker provider does NOT support:
- Pulling images
- Building images
- Creating new containers
- Removing containers
- Modifying container configuration

These are destructive or resource-intensive operations that belong in
the Unraid UI, not a monitoring dashboard.

### Self-Protection
The provider must never restart its own container. It should detect
the Great Eye container ID on startup (via hostname or container
inspection) and exclude it from action targets.

### Socket Mount Mode
- Read-only mount (`:ro`): Container list, stats, inspection work.
  Restart/start/stop actions will fail with permission error.
- Read-write mount: All operations available.
- The Unraid template should default to read-only and document the
  trade-off of enabling read-write.

## Metrics to Track
| Metric                           | Value Source                    | Tags                     |
|----------------------------------|--------------------------------|--------------------------|
| docker.containers_running        | container list count           | instance_id              |
| docker.containers_stopped        | container list count           | instance_id              |
| docker.containers_unhealthy      | container list count           | instance_id              |
| docker.container_cpu_percent     | per-container stats            | instance_id, container   |
| docker.container_memory_bytes    | per-container stats            | instance_id, container   |

Per-container metrics are tagged with the container name for filtering.
Tracking CPU and memory over time reveals resource trends and helps
identify containers that are leaking memory.

## Error Handling

### Socket Not Found
If the Docker socket doesn't exist at the configured path, this likely
means the volume mount is missing from the container configuration.
The error message should direct the user to check their Unraid template
or docker-compose config.

### Permission Denied
If the socket exists but returns permission errors, the container user
may not have access to the Docker group. The Unraid template should
handle this via PUID/PGID configuration.

### Container Stats Timeout
`/stats?stream=false` can occasionally be slow for containers under
heavy I/O. The provider should apply a short timeout (3s) and return
partial data if some container stats fail.

## Validate Config
```python
async def validate_config(self) -> tuple[bool, str]:
    try:
        transport = httpx.AsyncHTTPTransport(
            uds=self.config["socket_path"]
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://docker"
        ) as client:
            response = await client.get("/version")
            if response.status_code == 200:
                data = response.json()
                version = data.get("Version", "unknown")
                return True, f"Connected to Docker Engine v{version}"
            return False, f"Unexpected response: HTTP {response.status_code}"
    except FileNotFoundError:
        return False, f"Docker socket not found at {self.config['socket_path']}"
    except PermissionError:
        return False, "Permission denied — check socket permissions"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"
```
