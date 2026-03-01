# Architecture

## Overview
Great Eye follows a three-layer architecture: a provider layer that communicates
with upstream services, a core layer that manages scheduling/caching/auth, and
a presentation layer that serves HTMX-driven HTML to the browser. The backend
owns all upstream communication — the frontend never contacts external services.

```
┌─────────────────────────────────────────────────────┐
│  Browser (HTMX)                                     │
│  - Polls for partial HTML updates                   │
│  - Triggers actions via hx-post                     │
│  - No direct API calls to upstream services         │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP (HTML partials + forms)
┌──────────────────────▼──────────────────────────────┐
│  FastAPI Application                                │
│  ┌────────────┐ ┌──────────┐ ┌───────────────────┐  │
│  │ Routes     │ │ Auth     │ │ Permissions       │  │
│  │ (HTML +    │ │ (Plex +  │ │ (per-action       │  │
│  │  actions)  │ │  local)  │ │  checks)          │  │
│  └─────┬──────┘ └──────────┘ └───────────────────┘  │
│        │                                            │
│  ┌─────▼──────────────────────────────────────────┐  │
│  │ Provider Registry                              │  │
│  │ - Manages provider instances                   │  │
│  │ - Routes requests to correct instance          │  │
│  │ - Tracks health state                          │  │
│  └─────┬──────────────────────────────────────────┘  │
│        │                                            │
│  ┌─────▼──────┐ ┌──────────┐ ┌───────────────────┐  │
│  │ Scheduler  │ │ Cache    │ │ MetricsStore      │  │
│  │ (APSched)  │ │ (SQLite) │ │ (abstraction)     │  │
│  └─────┬──────┘ └──────────┘ └───────────────────┘  │
└────────┼────────────────────────────────────────────┘
         │ HTTP / Socket
┌────────▼────────────────────────────────────────────┐
│  Upstream Services                                  │
│  qBit, Sonarr, Radarr, Prowlarr, Overseerr,        │
│  Plex, Tautulli, Pi-hole, Unbound, Docker           │
└─────────────────────────────────────────────────────┘
```

## Provider System

### Lifecycle
Providers go through a defined lifecycle from registration to active polling:

1. **Registration** — provider class is imported and registers its type metadata
   with the provider registry (type ID, display name, config fields, permissions,
   default polling intervals). This happens at app startup via auto-discovery
   of modules in `src/providers/`.

2. **Configuration** — admin creates a provider instance via the UI, supplying
   a display name, URL, API key (encrypted before storage), and any
   provider-specific settings. Stored in the `provider_instances` table.

3. **Initialization** — on app startup (or when a new instance is created),
   the registry instantiates the provider class with its config and runs an
   initial health check.

4. **Active** — the scheduler polls the provider at configured intervals.
   Results are written to the cache. The provider is available for on-demand
   actions from users.

5. **Degraded / Down** — if health checks fail, the provider transitions to
   degraded or down state. Polling continues (to detect recovery) but the
   dashboard reflects the current state. Actions may be unavailable.

6. **Disabled** — admin can disable a provider instance. Polling stops, the
   instance is hidden from non-admin users, but config is preserved.

### Instance Model
Every provider is multi-instance by default. The registry maps:
```
Provider Type (class) → [Instance 1 (config A), Instance 2 (config B), ...]
```

Example:
```
SonarrProvider → [
  Instance "Sonarr (HD)"  → url: 10.0.0.45:8989,  api_key: encrypted_xxx
  Instance "Sonarr (4K)"  → url: 10.0.0.45:8990,  api_key: encrypted_yyy
]
```

Each instance has its own:
- Health state (up / degraded / down)
- Polling schedule
- Cached data
- Action history

### Auto-Discovery
On startup, the app scans `src/providers/` for modules that contain a class
inheriting from `BaseProvider`. Each discovered class is registered with the
provider registry. No manual import list to maintain.

To add a new provider:
1. Create `src/providers/my_service.py`
2. Define a class inheriting `BaseProvider`
3. Implement required methods
4. Restart the app — it appears in the admin UI for configuration

### Error Handling & Resilience
Providers must never crash the application. All upstream communication is
wrapped in error handling at two levels:

**Provider level** — each provider method catches exceptions from upstream
API calls and translates them to appropriate health states or error responses.
Timeouts, connection errors, and unexpected responses are all handled.

**Registry level** — the registry wraps all provider method calls in a safety
layer. If a provider raises an unhandled exception, the registry catches it,
logs it, marks the instance as degraded, and returns a safe fallback response.

**Timeout policy:**
- Health checks: 5 second timeout
- Summary data: 10 second timeout
- Detail data: 15 second timeout
- Actions: 30 second timeout

**Retry policy:**
- Health checks: no retry (next poll will try again)
- Data fetches: 1 retry with exponential backoff
- Actions: no automatic retry (user can retry manually)

## Scheduling & Polling

### APScheduler Configuration
The scheduler runs as a background task within the FastAPI process (no separate
worker). It uses the AsyncIO scheduler with a SQLite job store for persistence
across restarts.

### Polling Tiers
Each provider instance has three independent polling intervals:

| Tier    | Default Interval | Purpose                        | Cache TTL    |
|---------|------------------|--------------------------------|--------------|
| Health  | 30 seconds       | Is the service reachable?      | 30 seconds   |
| Summary | 60 seconds       | Dashboard card metrics         | 60 seconds   |
| Detail  | On-demand        | Drill-down data (fetched when  | 30 seconds   |
|         | (+ 5 min cache)  | user opens detail view, cached |              |
|         |                  | briefly for rapid navigation)  |              |

Intervals are configurable per-instance by admin. Minimum allowed intervals:
- Health: 10 seconds
- Summary: 30 seconds
- Detail cache: 10 seconds

### Poll Execution Flow
```
Scheduler triggers poll for Instance X, Tier Y
  → Registry checks if instance is enabled
  → Registry calls provider method with timeout
  → On success:
      - Update cached data in SQLite
      - Update health state if health tier
      - Write to MetricsStore if metrics-worthy data point
  → On failure:
      - Log error with instance context
      - Update health state (degraded/down)
      - Retain previous cached data (stale but shown with indicator)
      - Increment failure counter
  → On recovery (was down, now up):
      - Log recovery
      - Clear failure counter
      - Optionally trigger a summary refresh immediately
```

### Stale Data Handling
When a provider is down, the dashboard still shows the last-known data with
a visual indicator that it's stale (timestamp + warning icon). This is
preferable to showing nothing — the user can see "Sonarr (4K) was last
reachable 5 minutes ago, and at that time had 3 items in queue."

## Caching Layer

### Cache Storage
All cached provider data lives in the `provider_cache` SQLite table:

```
provider_cache:
  instance_id   (FK to provider_instances)
  tier          (health | summary | detail)
  data          (JSON blob)
  fetched_at    (datetime UTC)
  is_stale      (boolean — set true when provider becomes unreachable)
```

### Cache Read Strategy
When the frontend requests data:
1. Check cache for the requested instance + tier
2. If cache exists and is within TTL → return cached data
3. If cache is stale (beyond TTL or marked stale) → return cached data
   with stale flag, trigger async refresh
4. If no cache exists → trigger synchronous fetch, return result

This means the frontend almost always gets an instant response. The only
blocking case is the very first request for a provider that has never been
polled.

### Cache Invalidation
- Regular polling replaces cache entries on schedule
- Actions that modify state (e.g., triggering a Sonarr search) invalidate
  the relevant cache entries, forcing a refresh on next request
- Admin disabling/removing a provider clears its cache entries
- Provider config changes (URL, API key) clear cache and trigger fresh poll

## MetricsStore Abstraction

### Interface
```python
class MetricsStore(ABC):
    async def write(self, metric: str, value: float, tags: dict, timestamp: datetime) -> None
    async def query(self, metric: str, start: datetime, end: datetime, 
                    tags: dict = None, aggregation: str = "avg",
                    bucket: str = "1h") -> list[DataPoint]
    async def retention_cleanup(self, older_than: datetime) -> int
```

### V1 Implementation (SQLite)
Metrics are stored in a `metrics` table with indexed columns for metric name,
timestamp, and tags. Adequate for weeks of data at per-minute granularity.
Retention cleanup runs daily via scheduler.

### Future Swap Targets
- **InfluxDB**: Natural fit — push to the existing InfluxDB 1.8 instance
  on the same server. Telegraf could even be the transport.
- **PostgreSQL**: If the app migrates to Postgres for config storage.
- **Discard/Push-only**: Write to an external system, don't store locally.

The key constraint: all code that writes or reads time-series data goes through
MetricsStore. No direct SQLite queries for metrics anywhere in application code.

## Frontend Architecture (HTMX)

### Philosophy
The server renders HTML. The browser is a thin client. HTMX handles partial
page updates without full reloads. No JavaScript framework, no JSON API for
the frontend, no client-side state management.

### Page Structure
```
base.html
├── Navigation sidebar (provider list, health indicators)
├── Content area
│   ├── dashboard.html (grid of summary cards)
│   ├── detail views (per-provider, loaded via hx-get)
│   └── admin pages (providers, users, loaded via hx-get)
└── Toast/notification area (for action feedback)
```

### Dashboard Data Delivery (Dual Mode)
The dashboard supports two delivery modes, toggled by the user via a
control in the UI. Preference is stored in the user's session and persists
across page loads. Default mode: SSE.

Both modes consume the same cached data written by the scheduler. The
backend is agnostic to which mode is active — providers and caching work
identically regardless.

**Mode 1: Server-Sent Events (SSE) — Default**
A single persistent connection pushes incremental HTML updates as provider
data changes. Only cards with new data are re-rendered.

```html
<!-- Dashboard container subscribes to SSE stream -->
<div hx-ext="sse" sse-connect="/dashboard/stream">

  <!-- Each card listens for its own named event -->
  <div sse-swap="summary:{instance_id}" hx-swap="innerHTML">
    <!-- Server pushes updated card HTML only when data changes -->
  </div>

  <!-- Health badges in sidebar -->
  <span sse-swap="health:{instance_id}" hx-swap="outerHTML">
    <!-- Green/yellow/red dot pushed on state change -->
  </span>

</div>
```

SSE backend implementation:
- Scheduler writes new cache data → publishes event to in-memory async bus
- Each SSE connection has its own asyncio.Queue
- Event bus fans out: one publish → copy to all connected client queues
- SSE handler reads from queue, renders HTML partial, pushes to client
- Named events: "health:{instance_id}", "summary:{instance_id}"
- Connection cleanup on tab close / disconnect
- HTMX SSE extension handles auto-reconnect (default 1s retry)

**Mode 2: Batch Polling (Fallback)**
A single HTMX request fetches all summary cards and health badges at once.
Simpler, no persistent connection, suitable for constrained clients.

```html
<!-- Single request replaces entire dashboard grid -->
<div hx-get="/dashboard/batch"
     hx-trigger="every 60s"
     hx-swap="innerHTML">
  <!-- All summary cards + health badges rendered together -->
</div>
```

**Initial page load (both modes):**
Regardless of mode, the initial GET /dashboard returns the full page with
all cards rendered from cache. In SSE mode, the stream connection opens
after page load and begins pushing incremental updates. In batch mode,
the polling timer starts after page load.

**Mode toggle:**
```html
<!-- Toggle in dashboard header -->
<button hx-post="/preferences/delivery-mode"
        hx-vals='{"mode": "sse"}'
        hx-swap="none"
        hx-on::after-request="location.reload()">
  <!-- Switches mode and reloads to activate -->
</button>
```

### HTMX Patterns

**Detail view loading:**
Clicking a summary card loads the detail view into the content area.
Same pattern in both modes — detail views are always on-demand.
```html
<div hx-get="/providers/{instance_id}/detail"
     hx-trigger="click"
     hx-target="#content-area"
     hx-swap="innerHTML"
     hx-push-url="true">
</div>
```

**Actions with feedback:**
Actions (search, import, pause) are POST requests that return a toast
notification. In SSE mode, the card auto-updates when the cache is
invalidated and refreshed. In batch mode, the next poll picks up changes.
```html
<button hx-post="/providers/{instance_id}/action/search"
        hx-vals='{"query": "..."}'
        hx-target="#toast-area"
        hx-swap="beforeend">
  Search
</button>
```

In SSE mode, actions that invalidate cache trigger an immediate event push,
so the user sees the result without waiting for the next poll cycle.

**Permission-aware rendering:**
The server omits action buttons/forms entirely for users who lack the
required permission. No client-side permission checking — if you can see
the button, you can use it.

### Dark Theme
- Dark background with high-contrast text
- Color-coded health states: green (up), amber (degraded), red (down), grey (disabled)
- Minimal CSS — no framework, custom properties for theming
- Consistent card-based layout for all provider summaries
- Responsive: usable on desktop and tablet (phone is not a priority)

### Loading States
- Initial page load shows skeleton cards with loading indicators
- HTMX requests show a subtle loading indicator on the target element
- Failed requests show an error state inline (not a modal or redirect)

## Request Flow Examples

### First-Time Setup
```
Browser → GET / (any route)
  → Setup middleware checks: SELECT COUNT(*) FROM users
  → If 0 users: redirect to /setup
  → If 1+ users: continue normal routing

Browser → GET /setup
  → If users exist: return 404
  → If no users: render setup wizard Step 1

Browser → POST /setup/account
  → Validate username + password
  → Create admin user (bcrypt hash password)
  → Optionally initiate Plex OAuth link
  → Render Step 2 (provider setup)

Browser → POST /setup/providers (optional)
  → For each provider: validate config, encrypt secrets, save instance
  → Run health checks, report results
  → Render Step 3 (summary)

Browser → POST /setup/complete
  → Create session for new admin user
  → Redirect to /dashboard
  → /setup routes now return 404 permanently
```

### Dashboard Load
```
Browser → GET /dashboard
  → Auth middleware checks session
  → Route handler queries provider registry for all enabled instances
  → For each instance, read cached summary data
  → Render dashboard.html with all summary cards (full page)
  → Return full HTML page

If SSE mode (default):
  Browser opens SSE connection → GET /dashboard/stream
    → Server creates asyncio.Queue for this client
    → Pushes HTML fragments as provider data changes
    → Only changed cards are sent

If Batch mode:
  Browser (HTMX, every 60s) → GET /dashboard/batch
    → Auth middleware checks session
    → Read all cached summaries + health states
    → Render all cards as single HTML fragment
    → Return HTML fragment replacing dashboard grid
```

### User Triggers Sonarr Search
```
Browser → POST /providers/{instance_id}/action/search
  → Auth middleware checks session
  → Permission middleware checks user has "sonarr.search" permission
  → Route handler calls registry.execute_action(instance_id, "search", params)
  → Registry delegates to SonarrProvider.execute_action()
  → Provider calls Sonarr API (with timeout)
  → On success:
      - Invalidate summary cache for this instance
      - Trigger immediate cache refresh
      - Return success toast HTML to browser
      - SSE mode: cache refresh publishes event → card auto-updates
      - Batch mode: next poll picks up refreshed cache
  → On failure: return error toast HTML
```

### Provider Goes Down
```
Scheduler → poll health for Sonarr (4K)
  → Provider.health_check() times out after 5 seconds
  → Registry updates instance state to "down"
  → Registry marks cached data as stale
  → Logs warning with instance context

Next dashboard poll (HTMX):
  → GET /providers/{id}/summary returns last-known data
  → HTML includes "stale" indicator with timestamp
  → GET /providers/{id}/health-badge returns red dot

Scheduler continues polling:
  → Next health check succeeds
  → Registry updates state to "up", clears stale flag
  → Triggers immediate summary refresh
  → Dashboard shows fresh data on next HTMX poll
```

## Security Considerations

### Upstream API Keys
- Stored encrypted (Fernet) in provider_instances table
- Decrypted in-memory only when making API calls
- Never included in HTML responses, logs, or error messages
- Admin UI shows masked values (last 4 characters only)

### Session Management
- Signed cookies using SECRET_KEY
- Session expiry: configurable, default 24 hours
- Session invalidated on password change or admin revocation

### Input Validation
- All provider config validated before storage (URL format, required fields)
- Action parameters validated at the route level before reaching providers
- Provider URLs restricted to http/https schemes (no file://, no javascript://)

### Docker Socket Access
- The Docker provider communicates via the mounted socket
- This grants broad access to the Docker daemon — documented in deployment notes
- The Docker provider only performs read operations and container restart
- No image pulls, no container creation, no volume management
