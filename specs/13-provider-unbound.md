# Provider: Unbound

## Overview
The Unbound provider monitors the Unbound recursive DNS resolver via
the `unbound-control` command-line interface. Unbound sits behind
Pi-hole in the DNS chain, resolving queries directly against root
nameservers rather than forwarding to third-party DNS providers.

Unlike every other provider in Great Eye, Unbound does not expose an
HTTP API. Statistics are collected by executing `unbound-control
stats_noreset` on the host where Unbound runs. This creates a unique
integration challenge.

Multiple instances expected (2 — matching Pi-hole instances).

## Access Pattern
Unbound uses a TLS-based control interface rather than HTTP:

```
unbound-control -c /etc/unbound/unbound.conf stats_noreset
```

This returns key=value pairs on stdout, one per line.

### Integration Options

**Option A: Remote unbound-control over TLS (Preferred)**
`unbound-control` supports remote connections if `control-enable: yes`
is set in unbound.conf and the control keys are distributed. Great Eye
can connect over TLS to port 8953 (default) from the container.

```python
# TCP connection to unbound-control port
async def _fetch_stats(self) -> dict:
    """Connect to unbound-control over TLS and fetch stats."""
    # Use asyncio to connect to the control socket
    # Send "UBCT1 stats_noreset\n"
    # Parse key=value response
```

**Option B: HTTP wrapper sidecar**
A lightweight HTTP wrapper runs alongside Unbound, executes
`unbound-control stats_noreset`, and exposes the output as JSON.
This is simpler from Great Eye's perspective (just another HTTP
provider) but adds a deployment dependency.

**Option C: SSH exec (Fallback)**
SSH into the Unbound host and execute the command. Most complex,
requires SSH key management.

**DECISION: Option A (remote unbound-control over TLS)** is the cleanest.
If this proves too complex for v1, fall back to Option B with a simple
sidecar script. The provider abstraction allows swapping implementation
without changing the dashboard interface.

## Config Schema
```json
{
  "fields": [
    {
      "key": "host",
      "label": "Unbound Host",
      "type": "string",
      "required": true,
      "placeholder": "10.0.0.1",
      "help_text": "IP address of the Unbound server"
    },
    {
      "key": "port",
      "label": "Control Port",
      "type": "integer",
      "required": true,
      "default": 8953,
      "help_text": "unbound-control port (default 8953)"
    },
    {
      "key": "server_cert",
      "label": "Server Certificate",
      "type": "secret",
      "required": true,
      "help_text": "Path or content of unbound_server.pem"
    },
    {
      "key": "control_key",
      "label": "Control Key",
      "type": "secret",
      "required": true,
      "help_text": "Path or content of unbound_control.key"
    },
    {
      "key": "control_cert",
      "label": "Control Certificate",
      "type": "secret",
      "required": true,
      "help_text": "Path or content of unbound_control.pem"
    }
  ]
}
```

If using Option B (HTTP sidecar), the config simplifies to just
`url` + optional `api_key`.

## Default Polling Intervals
```json
{
  "health_seconds": 60,
  "summary_seconds": 60,
  "detail_cache_seconds": 300
}
```

## Permissions
| Key                 | Display Name              | Category | Notes                          |
|---------------------|---------------------------|----------|--------------------------------|
| unbound.view        | View Unbound Data         | read     | Resolver stats, cache info     |
| unbound.flush       | Flush Cache               | action   | Clear the DNS cache            |

## Health Check

### Method
Execute `stats_noreset` and verify response contains expected keys.

### Status Mapping
| Condition                          | Status   | Message                           |
|------------------------------------|----------|-----------------------------------|
| Stats returned, reasonable values  | UP       | "Resolving ({queries} queries)"  |
| Stats returned, high unwanted      | DEGRADED | "High unwanted traffic"          |
| Connection refused / TLS error     | DOWN     | "Control connection failed"      |
| Timeout                            | DOWN     | "Connection timed out"            |

## Summary Data

### Source
`unbound-control stats_noreset` output, parsed from key=value pairs.

### Key Statistics Collected
```
total.num.queries              # Total queries received
total.num.cachehits            # Answered from cache
total.num.cachemiss            # Required recursion
total.num.prefetch             # Prefetch queries
total.num.recursivereplies     # Recursive replies sent
total.num.expired              # Served expired entries
total.requestlist.avg          # Avg items in request list
total.requestlist.max          # Max items in request list
total.recursion.time.avg       # Avg recursion time (seconds)
total.recursion.time.median    # Median recursion time (seconds)
msg.cache.count                # Items in message cache
rrset.cache.count              # Items in rrset cache
infra.cache.count              # Items in infra cache
unwanted.queries               # Refused/dropped queries
unwanted.replies               # Unsolicited replies (spoofing?)
```

### Summary Data Shape
```json
{
  "queries": {
    "total": 218959,
    "cache_hits": 216339,
    "cache_misses": 2620,
    "cache_hit_rate": 98.8,
    "prefetch": 28326,
    "expired_served": 21661,
    "recursive_replies": 2620
  },
  "performance": {
    "recursion_time_avg_ms": 45.2,
    "recursion_time_median_ms": 12.1,
    "request_list_avg": 0.5,
    "request_list_max": 15
  },
  "cache": {
    "message_count": 2333,
    "rrset_count": 2034,
    "infra_count": 3
  },
  "security": {
    "unwanted_queries": 0,
    "unwanted_replies": 0
  }
}
```

### Summary Card Display
- Cache hit rate (prominent percentage, green if > 90%)
- Total queries resolved
- Avg recursion time
- Cache sizes (message, rrset)
- Unwanted traffic indicator (red if > 0, possible spoof attack)

## Detail Data
Same as summary — Unbound's stats are flat (no drill-down endpoints).
Detail view can show the full stat dump in a formatted table,
plus historical charts from metrics.

### Detail View Display

**Stats tab:**
- Full statistics table with all key=value pairs
- Grouped by category (queries, cache, recursion, security)

**Performance tab:**
- Recursion time avg/median over time (from metrics)
- Cache hit rate over time
- Request list utilization

**Security tab:**
- Unwanted queries/replies over time
- Spoof detection: sharp increase in unwanted.replies

## Actions

### flush_cache
Flush the entire Unbound DNS cache.

| Field        | Value                                        |
|--------------|----------------------------------------------|
| Key          | flush_cache                                  |
| Permission   | unbound.flush                                |
| Confirm      | true                                         |
| Confirm Msg  | "Flush Unbound cache? All cached DNS records will be cleared." |
| Params       | none                                         |

**Command:** `unbound-control flush_zone .`

This clears all cached entries. Queries will need to be resolved
recursively until the cache warms up again.

## Metrics to Track
| Metric                           | Value Source                    | Tags              |
|----------------------------------|--------------------------------|--------------------|
| unbound.queries_total            | total.num.queries              | instance_id        |
| unbound.cache_hits               | total.num.cachehits            | instance_id        |
| unbound.cache_misses             | total.num.cachemiss            | instance_id        |
| unbound.cache_hit_rate           | computed percentage            | instance_id        |
| unbound.recursion_time_avg       | total.recursion.time.avg       | instance_id        |
| unbound.unwanted_queries         | unwanted.queries               | instance_id        |
| unbound.unwanted_replies         | unwanted.replies               | instance_id        |
| unbound.msg_cache_count          | msg.cache.count                | instance_id        |

## Error Handling

### Control Interface Not Enabled
If `control-enable: no` in unbound.conf, the connection will be
refused. The validate_config should detect this and provide clear
instructions: "Enable remote-control in unbound.conf."

### Certificate Mismatch
TLS connections use certificates generated by `unbound-control-setup`.
If the certs don't match, the connection fails. Error message should
guide the user to regenerate or recopy certificates.

### stats vs stats_noreset
The provider uses `stats_noreset` which does NOT reset counters after
reading. This is important — if we used `stats`, polling would reset
the counters and break any other monitoring tools. The `_noreset`
variant reads without side effects.

Note: Stats may be cumulative or per-interval depending on the
`statistics-cumulative` setting in unbound.conf. The provider should
handle both modes. If cumulative, compute deltas between polls for
rate metrics. If per-interval, use values directly.

## Validate Config
```python
async def validate_config(self) -> tuple[bool, str]:
    try:
        stats = await self._fetch_stats()
        if stats and "total.num.queries" in stats:
            queries = stats["total.num.queries"]
            return True, f"Connected ({queries} total queries)"
        return False, "Connected but unexpected response format"
    except ConnectionRefusedError:
        return False, "Control connection refused — is control-enable set to yes?"
    except ssl.SSLError:
        return False, "TLS certificate error — check control keys"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"
```
