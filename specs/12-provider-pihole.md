# Provider: Pi-hole

## Overview
The Pi-hole provider connects to Pi-hole's REST API to monitor DNS
blocking statistics, query activity, and blocking status. Pi-hole is
the network-level ad blocker — it intercepts DNS queries and blocks
ads/trackers before they reach devices.

This provider targets Pi-hole v6, which introduced a completely new
REST API at `/api/`. The legacy v5 API (`/admin/api.php`) is NOT
supported. Users on v5 should upgrade to v6 before adding Pi-hole
to Great Eye.

Multiple instances expected (2 — primary and secondary DNS).

## Upstream API
- Base path: `/api/`
- Auth: Session-based (POST password to `/api/auth`, receive SID cookie)
- Default port: 80 (v6 embedded web server), fallback 8080
- Response format: JSON
- Documentation: Self-hosted at `http://pi.hole/api/docs`
- Online docs: https://ftl.pi-hole.net/master/docs/

### Auth Notes
Pi-hole v6 uses session-based authentication:

1. POST `/api/auth` with `{"password": "your_password"}`
2. Response includes a session ID (SID) as a cookie
3. SID is included on subsequent requests

If no password is set on the Pi-hole instance, some endpoints are
accessible without auth. The provider should always attempt auth.

```python
async def _authenticate(self):
    """Authenticate to Pi-hole and store session cookie."""
    response = await self.http_client.post("/api/auth", json={
        "password": self.config["password"]
    })
    if response.status_code == 200:
        data = response.json()
        session = data.get("session", {})
        if session.get("valid"):
            self.sid = session.get("sid")
            self.http_client.cookies.set("sid", self.sid)
            return True
    return False
```

Session validity should be checked periodically. If a request returns
401, re-authenticate before retrying.

## Config Schema
```json
{
  "fields": [
    {
      "key": "url",
      "label": "Pi-hole URL",
      "type": "url",
      "required": true,
      "placeholder": "http://10.0.0.1",
      "help_text": "URL to Pi-hole web interface (port 80 or 8080)"
    },
    {
      "key": "password",
      "label": "Web Interface Password",
      "type": "secret",
      "required": false,
      "help_text": "Pi-hole web interface password (leave blank if no password set)"
    }
  ]
}
```

Note: Unlike *arr providers which use a persistent API key, Pi-hole v6
uses the web interface password for session auth. The provider must
handle session lifecycle (login, refresh, expiry).

## Default Polling Intervals
```json
{
  "health_seconds": 30,
  "summary_seconds": 60,
  "detail_cache_seconds": 300
}
```

## Permissions
| Key                  | Display Name              | Category | Notes                          |
|----------------------|---------------------------|----------|--------------------------------|
| pihole.view          | View Pi-hole Data         | read     | Stats, query log, lists        |
| pihole.blocking      | Toggle Blocking           | action   | Enable/disable DNS blocking    |
| pihole.gravity       | Update Gravity            | action   | Refresh blocklists             |

## Health Check

### Endpoint
```
GET /api/dns/blocking
```

This lightweight endpoint returns whether blocking is enabled. If it
responds, Pi-hole's FTL engine is running.

### Status Mapping
| Condition                          | Status   | Message                           |
|------------------------------------|----------|-----------------------------------|
| 200, blocking enabled              | UP       | "Blocking active"                |
| 200, blocking disabled             | DEGRADED | "Blocking disabled"              |
| 200, response > 3s                | DEGRADED | "Slow response ({time}ms)"       |
| 401 (auth required, no password)  | DEGRADED | "Auth required — set password"   |
| Connection refused                 | DOWN     | "Connection refused"              |
| Timeout (5s)                       | DOWN     | "Connection timed out"            |

If blocking is disabled, DEGRADED rather than UP — this is a
configuration issue the admin should know about.

## Summary Data

### Endpoints Used (parallel via asyncio.gather)
```
GET /api/stats/summary          # Main statistics summary
GET /api/dns/blocking           # Blocking status
```

### Summary Data Shape
```json
{
  "queries": {
    "total": 36692,
    "blocked": 9013,
    "percent_blocked": 24.6,
    "unique_domains": 2971,
    "forwarded": 7748,
    "cached": 19610
  },
  "clients": {
    "active": 15,
    "total": 22
  },
  "gravity": {
    "domains_being_blocked": 450350,
    "last_update": "2025-06-15T04:00:00Z"
  },
  "blocking_enabled": true,
  "version": {
    "core": "6.2",
    "ftl": "6.3",
    "web": "6.3"
  }
}
```

### Summary Card Display
- Blocking status indicator (green = active, red = disabled)
- Queries today: total, blocked, percentage blocked
- Domains on blocklist count
- Active clients count
- Mini chart: queries over time (if metrics available)
- Cached vs forwarded breakdown

## Detail Data

### Endpoints Used
```
GET /api/stats/summary
GET /api/stats/top_domains?count=20          # Top permitted domains
GET /api/stats/top_blocked?count=20          # Top blocked domains
GET /api/stats/top_clients?count=20          # Top clients
GET /api/stats/upstreams                     # Upstream DNS servers
GET /api/stats/query_types                   # Query type distribution
GET /api/stats/history                       # Query history over time
GET /api/dns/blocking
GET /api/info/version
```

### Detail View Display

**Overview tab:**
- Full statistics summary with percentages
- Query type breakdown (A, AAAA, CNAME, etc.)
- Upstream DNS usage distribution
- Gravity last update timestamp

**Top Domains tab:**
- Top permitted domains (by query count)
- Top blocked domains
- Per-domain: query count, client count

**Clients tab:**
- Top clients by query count
- Per-client: total queries, blocked queries, percentage

**History tab:**
- Query volume over time (chart-ready data)
- Blocked vs allowed over time

## Actions

### disable_blocking
Temporarily disable DNS blocking.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | disable_blocking                             |
| Permission   | pihole.blocking                              |
| Confirm      | true                                         |
| Confirm Msg  | "Disable DNS blocking? Ads will not be blocked." |
| Params       | `{"duration": 300}`                          |

**Endpoint:** `POST /api/dns/blocking`
```json
{"blocking": false, "timer": 300}
```

Duration in seconds (0 = indefinite). Timer auto-re-enables blocking.

### enable_blocking
Re-enable DNS blocking.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | enable_blocking                              |
| Permission   | pihole.blocking                              |
| Confirm      | false                                        |
| Params       | none                                         |

**Endpoint:** `POST /api/dns/blocking`
```json
{"blocking": true}
```

### update_gravity
Trigger a blocklist update (gravity refresh).

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | update_gravity                               |
| Permission   | pihole.gravity                               |
| Confirm      | false                                        |
| Params       | none                                         |

**Endpoint:** `POST /api/action/gravity`

This is a long-running operation. The API accepts the request and
gravity runs in the background. No completion callback.

## Metrics to Track
| Metric                           | Value Source                    | Tags              |
|----------------------------------|--------------------------------|--------------------|
| pihole.queries_total             | stats/summary total            | instance_id        |
| pihole.queries_blocked           | stats/summary blocked          | instance_id        |
| pihole.percent_blocked           | stats/summary percent          | instance_id        |
| pihole.domains_on_blocklist      | gravity domains count          | instance_id        |
| pihole.active_clients            | stats/summary clients          | instance_id        |
| pihole.queries_cached            | stats/summary cached           | instance_id        |
| pihole.queries_forwarded         | stats/summary forwarded        | instance_id        |

## Error Handling

### Session Expiry
Pi-hole sessions expire. If any request returns 401:
1. Re-authenticate with stored password
2. Retry the failed request once
3. If re-auth fails, mark as DEGRADED with "Session expired"

### No Password Configured
Some Pi-hole instances have no password. The provider should:
- Attempt unauthenticated access first
- If 401, try authenticating with configured password
- If no password configured and 401, show "Password required"

### v5 Detection
If the provider connects and gets responses that look like the old
`/admin/api.php` format (or gets redirected to `/admin/`), surface:
"This appears to be Pi-hole v5. Please upgrade to v6."

## Validate Config
```python
async def validate_config(self) -> tuple[bool, str]:
    try:
        # Try authenticating
        if self.config.get("password"):
            auth_ok = await self._authenticate()
            if not auth_ok:
                return False, "Invalid password"

        # Check API is responding
        response = await self.http_client.get("/api/info/version")
        if response.status_code == 401:
            return False, "Authentication required — set password"
        if response.status_code != 200:
            return False, f"Unexpected response: HTTP {response.status_code}"

        data = response.json()
        version = data.get("version", {})
        ftl = version.get("ftl", {}).get("version", "unknown")
        return True, f"Connected to Pi-hole (FTL v{ftl})"

    except httpx.ConnectError:
        return False, f"Cannot connect to {self.config['url']}"
    except Exception as e:
        return False, f"Connection test failed: {str(e)}"
```
