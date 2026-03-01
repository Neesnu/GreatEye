# Frontend: HTMX + Dark Theme

## Overview
Great Eye's frontend is server-rendered HTML enhanced with HTMX for
partial page updates. There is no JavaScript framework, no JSON API
consumed by the browser, and no client-side state management. The
server decides what HTML to send based on the user's permissions and
the current state of all providers.

## Technology Stack
- **Templating:** Jinja2 (via FastAPI/Starlette)
- **Interactivity:** HTMX 2.x + SSE extension
- **Styling:** Custom CSS (no framework), dark theme
- **Icons:** Lucide Icons (SVG, self-hosted subset)
- **Charts:** Lightweight inline SVG sparklines (server-rendered),
  or Chart.js for detail view charts loaded on demand
- **No build step:** CSS and JS served as static files, no bundler

## Layout Structure

### Shell
Every page shares a common shell:

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Great Eye{% endblock %}</title>
  <link rel="stylesheet" href="/static/css/greateye.css">
  <script src="/static/js/htmx.min.js"></script>
  <script src="/static/js/htmx-sse.js"></script>
</head>
<body>
  <nav id="sidebar">{% include "partials/sidebar.html" %}</nav>
  <main id="content">{% block content %}{% endblock %}</main>
  <div id="toast-container"></div>
</body>
</html>
```

### Sidebar Navigation
Persistent left sidebar (collapsible on mobile):
- **Dashboard** — main grid view
- **Providers** — grouped by category
  - Media Management (Sonarr, Radarr, Prowlarr)
  - Downloads (qBittorrent)
  - Requests (Seerr)
  - Playback (Plex, Tautulli)
  - DNS / Network (Pi-hole, Unbound)
  - System (Docker)
- **Admin** (if user has admin role)
  - Provider Config
  - Users & Roles
  - System Settings

Active provider instances appear under their category with health
dot indicators (green/amber/red/grey).

### Responsive Behavior
- Desktop (>1024px): sidebar visible, dashboard grid 3-4 columns
- Tablet (768-1024px): sidebar collapsible, grid 2 columns
- Mobile (<768px): sidebar hidden (hamburger), grid 1 column

## Dashboard Page

### Grid Layout
The dashboard is a CSS Grid of provider summary cards. Each card is
an independently updatable unit identified by its instance ID.

```html
<div id="dashboard-grid"
     hx-ext="sse"
     sse-connect="/dashboard/stream">

  {% for instance in instances %}
  <div class="card card--{{ instance.health_status }}"
       id="card-{{ instance.id }}"
       sse-swap="summary:{{ instance.id }}"
       hx-swap="innerHTML">
    {% include "cards/" ~ instance.provider_type ~ ".html" %}
  </div>
  {% endfor %}

</div>
```

### Card Anatomy
Every summary card follows the same structure:

```
┌─────────────────────────────────────┐
│ [icon] Instance Name      [●] [⚙]  │  ← Header: icon, name, health dot, settings
│─────────────────────────────────────│
│                                     │
│  Primary metric / status            │  ← Hero area: the one number that matters
│                                     │
│  Secondary metrics                  │  ← Supporting info (2-3 lines)
│  Key list / mini table              │  ← Context-specific content
│                                     │
│─────────────────────────────────────│
│ Last updated: 30s ago    [Refresh]  │  ← Footer: freshness indicator, manual refresh
└─────────────────────────────────────┘
```

Cards are sized uniformly (min-width 320px). Content adapts to
available space but the grid cell size is consistent.

### Health Dot Colors
| Status   | Color                | CSS Variable           |
|----------|----------------------|------------------------|
| UP       | Green (#22c55e)      | --color-health-up      |
| DEGRADED | Amber (#f59e0b)      | --color-health-degraded|
| DOWN     | Red (#ef4444)        | --color-health-down    |
| UNKNOWN  | Grey (#6b7280)       | --color-health-unknown |

### Stale Data Indicator
When cache data is stale (provider is down, showing last known values):

```html
<div class="card card--stale">
  <div class="stale-banner">
    ⚠ Last updated 5 minutes ago — provider unreachable
  </div>
  <!-- normal card content with last known data -->
</div>
```

## SSE Data Delivery

### Connection
```html
<div hx-ext="sse" sse-connect="/dashboard/stream">
```

HTMX opens a single SSE connection. The server sends named events
when cache data changes.

### Event Types
```
event: health:{instance_id}
data: <div class="health-dot health--up" title="Connected (v4.6.1)"></div>

event: summary:{instance_id}
data: <div class="card-body">...full card body HTML...</div>
```

### Reconnection
HTMX SSE extension auto-reconnects with 1-second retry. On reconnect,
the server sends a full state dump (all current summaries) to ensure
the client is synchronized.

## Batch Polling Fallback

### Toggle
```html
<button hx-post="/preferences/delivery-mode"
        hx-vals='{"mode": "batch"}'
        hx-swap="none">
  Switch to Polling
</button>
```

### Polling Request
```html
<div id="dashboard-grid"
     hx-get="/dashboard/cards"
     hx-trigger="load, every 60s"
     hx-swap="innerHTML">
</div>
```

## Detail Views

### Navigation
Clicking a card header opens the detail view for that instance:

```html
<a hx-get="/providers/{{ instance.id }}/detail"
   hx-target="#content"
   hx-push-url="true">
  Instance Name
</a>
```

HTMX replaces the `#content` area and updates the browser URL.
The back button works via `hx-push-url`.

### Tab Pattern
Detail views use tabs for sub-sections:

```html
<div class="tabs">
  <button class="tab tab--active"
          hx-get="/providers/{{ id }}/detail/overview"
          hx-target="#detail-content">Overview</button>
  <button class="tab"
          hx-get="/providers/{{ id }}/detail/queue"
          hx-target="#detail-content">Queue</button>
</div>
<div id="detail-content">
  {% include "detail/" ~ provider_type ~ "/overview.html" %}
</div>
```

Tabs load content via HTMX without full page reload.

### Detail Refresh
Detail views auto-refresh on a timer:

```html
<div id="detail-content"
     hx-get="/providers/{{ id }}/detail/{{ active_tab }}"
     hx-trigger="every 30s"
     hx-swap="innerHTML">
```

## Action Handling

### Simple Actions
```html
<button hx-post="/providers/{{ instance_id }}/actions/pause"
        hx-vals='{"hashes": ["abc123"]}'
        hx-confirm="Pause this torrent?"
        hx-target="#toast-container"
        hx-swap="beforeend">
  Pause
</button>
```

### Action Response
The server returns a toast notification:

```html
<div class="toast toast--success" role="alert"
     _="on load wait 5s then remove me">
  Torrent paused successfully
</div>
```

Note: `_="..."` uses hyperscript (HTMX companion) for the auto-dismiss.
Alternative: use `hx-on::after-settle` with a timeout. If hyperscript
adds too much weight, use a tiny inline script instead.

### Confirmation Dialogs
For destructive actions (delete, bulk operations):

```html
<button hx-post="/providers/{{ id }}/actions/delete_series"
        hx-vals='{"series_id": 1, "delete_files": true}'
        hx-confirm="Delete 'Show Name' and all files? This cannot be undone."
        hx-target="#toast-container"
        hx-swap="beforeend">
  Delete
</button>
```

`hx-confirm` uses the browser's native confirm dialog. For a styled
modal, swap to a modal pattern where the button loads a confirmation
partial into a modal container.

## Permission-Aware Rendering
The server omits UI elements the user can't use:

```jinja2
{% if has_permission("sonarr.search") %}
<button hx-post="/providers/{{ id }}/actions/search_episode"
        hx-vals='{"episode_ids": [{{ episode.id }}]}'>
  Search
</button>
{% endif %}
```

Viewers see data but no action buttons. Users see actions. Admins
see everything including delete and config.

## Dark Theme Design

### Color Palette
```css
:root {
  /* Backgrounds */
  --bg-primary: #0f1117;        /* Page background */
  --bg-secondary: #1a1d27;      /* Card background */
  --bg-tertiary: #252830;       /* Input/hover background */
  --bg-elevated: #2a2d37;       /* Modal/dropdown background */

  /* Text */
  --text-primary: #e4e4e7;      /* Primary text */
  --text-secondary: #a1a1aa;    /* Secondary/muted text */
  --text-tertiary: #71717a;     /* Disabled/placeholder text */

  /* Borders */
  --border-default: #2a2d37;    /* Default borders */
  --border-hover: #3f4350;      /* Hover state borders */

  /* Health colors */
  --color-health-up: #22c55e;
  --color-health-degraded: #f59e0b;
  --color-health-down: #ef4444;
  --color-health-unknown: #6b7280;

  /* Accent */
  --color-accent: #6366f1;      /* Indigo — buttons, links, focus */
  --color-accent-hover: #818cf8;

  /* Status colors (for download states, etc.) */
  --color-downloading: #3b82f6; /* Blue */
  --color-seeding: #22c55e;     /* Green */
  --color-paused: #6b7280;      /* Grey */
  --color-error: #ef4444;       /* Red */
  --color-warning: #f59e0b;     /* Amber */
  --color-stalled: #f59e0b;     /* Amber */
}
```

### Typography
- Font: System font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", ...`)
- Base size: 14px
- Headings: 600 weight, 1rem–1.5rem
- Monospace for technical values: `"JetBrains Mono", "Fira Code", monospace`

### Component Styles

**Cards:**
- Background: `--bg-secondary`
- Border: 1px `--border-default`, left border 3px health color
- Border-radius: 8px
- Subtle box-shadow for depth
- Hover: border shifts to `--border-hover`

**Tables (detail views):**
- Striped rows (alternating `--bg-secondary` and `--bg-tertiary`)
- Sticky header row
- Compact padding (8px 12px)
- Sortable columns (click header to sort, HTMX request)

**Progress bars:**
- Height: 6px, border-radius: 3px
- Background: `--bg-tertiary`
- Fill: gradient based on percentage (blue → green)
- Label above or beside: "50% (4.0 GB / 8.0 GB)"

**Buttons:**
- Primary: `--color-accent` background, white text
- Danger: `--color-error` background for destructive actions
- Ghost: transparent background, border on hover
- Small size for inline/table actions

**Toast notifications:**
- Fixed position bottom-right
- Auto-dismiss after 5 seconds
- Color-coded: green (success), red (error), amber (warning)

## Admin Pages

### Provider Configuration
- List of all configured instances with health status
- Add/edit/remove instances
- Config form generated from provider's config_schema JSON
- Secret fields show masked values, editable
- "Test Connection" button (calls validate_config via HTMX)
- Drag-and-drop sort order for dashboard card arrangement

### User Management
- User list with role, auth method, last login
- Create/edit local users
- Manage Plex approved users whitelist
- Role assignment
- Force password reset toggle
- Permission matrix view (roles × permissions)

### System Settings
- SECRET_KEY status (set/not set — never shown)
- Database size and metrics retention
- Delivery mode default
- About page with version info

## Static File Structure
```
static/
  css/
    greateye.css              # All styles in one file
  js/
    htmx.min.js               # HTMX core
    htmx-sse.js               # SSE extension
  icons/
    lucide/                    # SVG icon subset
      download.svg
      tv.svg
      film.svg
      shield.svg
      server.svg
      ...
```

## Template Structure
```
templates/
  base.html                    # Shell with head, sidebar, content block
  partials/
    sidebar.html               # Navigation sidebar
    toast.html                 # Toast notification partial
    confirm-modal.html         # Styled confirmation modal (if used)
  pages/
    dashboard.html             # Dashboard grid page
    login.html                 # Login page
    setup.html                 # First-time setup wizard
  cards/
    qbittorrent.html           # qBit summary card
    sonarr.html                # Sonarr summary card
    radarr.html                # Radarr summary card
    prowlarr.html              # Prowlarr summary card
    seerr.html                 # Seerr summary card
    plex.html                  # Plex summary card
    tautulli.html              # Tautulli summary card
    pihole.html                # Pi-hole summary card
    unbound.html               # Unbound summary card
    docker.html                # Docker summary card
  detail/
    qbittorrent/
      overview.html
      torrents.html
    sonarr/
      overview.html
      queue.html
      missing.html
      calendar.html
    radarr/
      overview.html
      queue.html
      missing.html
      calendar.html
    ...
  admin/
    providers.html             # Provider config list
    provider_form.html         # Add/edit provider instance
    users.html                 # User management
    roles.html                 # Role/permission management
    settings.html              # System settings
```

## HTMX Patterns Reference

### Pattern: Inline Edit
```html
<span hx-get="/providers/{{ id }}/edit/display_name"
      hx-trigger="click"
      hx-swap="outerHTML">
  {{ instance.display_name }}
</span>
```
Clicking swaps in an input field. Blur or Enter saves.

### Pattern: Search with Debounce
```html
<input type="search"
       hx-get="/providers/{{ id }}/detail/torrents"
       hx-trigger="keyup changed delay:300ms"
       hx-target="#torrent-list"
       hx-include="[name='sort'], [name='filter']"
       name="search"
       placeholder="Search torrents...">
```

### Pattern: Sortable Table Headers
```html
<th hx-get="/providers/{{ id }}/detail/queue?sort=progress&dir=desc"
    hx-target="#queue-table-body"
    class="sortable">
  Progress ▼
</th>
```

### Pattern: Pagination
```html
<button hx-get="/providers/{{ id }}/detail/history?page=2"
        hx-target="#history-list"
        hx-swap="innerHTML">
  Load More
</button>
```

### Pattern: Auto-Refresh Section
```html
<div hx-get="/providers/{{ id }}/detail/queue"
     hx-trigger="every 15s"
     hx-swap="innerHTML">
  <!-- queue content -->
</div>
```
