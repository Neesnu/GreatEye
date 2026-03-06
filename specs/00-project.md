# Project Overview

## Name
Great Eye

## Purpose
A self-hosted homelab operations dashboard that provides unified monitoring,
health checking, and interactive management across multiple containerized
services. Great Eye aggregates data from download clients, media managers,
DNS infrastructure, playback services, and the container runtime itself into
a single pane of glass — surfacing issues early and enabling common actions
without jumping between multiple web UIs.

The application uses a provider-based plugin architecture, allowing any
service integration to be added without modifying core code.

## Tech Stack
- Backend: Python 3.12 with FastAPI
- Database: SQLite via SQLAlchemy (config, auth, cached state)
- Data Layer: Abstraction boundary for metrics storage (SQLite v1, swappable)
- Frontend: HTML + HTMX + minimal CSS (dark theme)
- Background Tasks: APScheduler (provider polling, cache refresh)
- Encryption: Fernet symmetric encryption (cryptography library)
- Auth: Plex OAuth + local username/password
- Container: Single Dockerfile
- Server: Uvicorn

## Project Structure
```
src/
  main.py                  # FastAPI app, startup, scheduler init
  database.py              # SQLAlchemy models and database setup
  auth.py                  # Plex OAuth, local auth, session management
  permissions.py           # Permission registry, role checking
  encryption.py            # Fernet encrypt/decrypt for secrets at rest
  scheduler.py             # APScheduler setup, provider polling loops
  metrics.py               # MetricsStore abstraction (v1: SQLite backend)
  providers/
    base.py                # BaseProvider ABC — health, summary, detail, actions
    registry.py            # Provider registration, discovery, lifecycle
    qbittorrent.py         # qBittorrent provider
    sonarr.py              # Sonarr provider
    radarr.py              # Radarr provider
    prowlarr.py            # Prowlarr provider
    seerr.py               # Seerr provider (Overseerr compatible)
    plex.py                # Plex provider
    tautulli.py            # Tautulli provider
    pihole.py              # Pi-hole provider
    unbound.py             # Unbound provider
    docker.py              # Docker container runtime provider
  static/
    css/                   # Stylesheets (dark theme)
    js/                    # Minimal JS if needed beyond HTMX
  templates/
    base.html              # Layout shell
    dashboard.html         # Main dashboard grid
    login.html             # Auth page
    admin/
      providers.html       # Provider instance management
      users.html           # User management
    partials/              # HTMX partial templates per provider type
      health_card.html
      qbit_summary.html
      qbit_detail.html
      sonarr_summary.html
      ...
requirements.txt
Dockerfile
greateye.xml               # Unraid Community Applications template
```

## General Constraints
- All configuration stored in SQLite database
- Environment variables for initial setup only:
  - SECRET_KEY (Fernet encryption key + session signing)
  - PLEX_CLIENT_ID (for Plex OAuth flow)
  - DATABASE_URL (optional, defaults to sqlite:///data/greateye.db)
- No hardcoded credentials anywhere
- All API keys and tokens encrypted at rest using Fernet (keyed from SECRET_KEY)
- All routes except /login and /auth/* require authentication
- Backend handles ALL upstream API calls — frontend never contacts services directly
- Rate limiting on upstream API calls per-provider instance
- App runs on port 8484

## Authentication Model
- Two auth methods: Plex OAuth and local username/password
- Plex OAuth tokens encrypted at rest (Fernet)
- Local passwords hashed with bcrypt (one-way, not recoverable)
- Admin pre-approves Plex usernames for OAuth access
- Session-based auth with signed cookies (SECRET_KEY)
- First user created via browser-based setup wizard (see spec 02)
- Setup wizard only accessible when zero users exist in database
- Setup wizard also allows optional initial provider configuration

### Password Reset (v1)
- Admin can flag any local account for forced password reset
- Flagged users must set a new password on next login before accessing anything
- Users can request a reset from the login page:
  - Generates a time-limited reset token (1 hour expiry)
  - Token stored as a hash in the database (not plaintext)
  - Token/reset link surfaced to Admin in the admin UI (pending resets queue)
  - Admin shares the link with the user out-of-band
- User accounts have an optional email field (unused in v1, stored for future)
- Future: email delivery of reset tokens requires no schema or flow changes,
  only adding an email transport layer

## Authorization Model
- Permission-based access control (code checks permissions, not roles)
- Three default roles seeded on first run:
  - Admin: all permissions
  - User: view + safe actions (search, import, pause/resume)
  - Viewer: read-only across all providers
- Roles are bundles of permissions stored in the database
- Each provider registers the permissions it requires
- Permission format: provider_type.action (e.g., sonarr.search, pihole.disable)
- Role-to-permission mapping is database-driven and admin-editable
- Schema supports creating custom roles beyond the three defaults
- Future-proof: no role names hardcoded in application logic

## Data Storage Strategy
- Config & Auth: SQLite (permanent home)
- Provider cached state: SQLite (polled data for fast frontend rendering)
- Historical metrics: SQLite in v1, behind MetricsStore abstraction
- MetricsStore interface designed for future swap to InfluxDB, PostgreSQL,
  or push-to-existing-stack (Telegraf → InfluxDB) without core refactor
- All time-series writes go through MetricsStore, never direct SQLite calls

## Provider Architecture (Summary)
- Every service integration is a Provider
- Providers implement a BaseProvider interface:
  - health_check() → HealthStatus (up | degraded | down) + message
  - get_summary() → dict of key metrics for dashboard card
  - get_detail() → dict of detailed data for drill-down view
  - get_actions() → list of available actions with required permissions
  - execute_action(action, params) → result
- Providers self-register with:
  - Type identifier (e.g., "qbittorrent", "sonarr")
  - Display name and icon
  - Required configuration fields (url, api_key, etc.)
  - Default polling intervals (health, summary, detail)
  - Permission definitions
- Multi-instance is first-class: same provider class, different config per instance
- Provider instances are configured by Admin via UI (URL, API key, display name)
- Remote hosts fully supported (e.g., Pi-hole on Raspberry Pi at different IP)
- Graceful degradation: unreachable providers report "down" status, never throw

## MVP Provider Scope
| Provider       | Instances | Category         |
|----------------|-----------|------------------|
| qBittorrent    | 2         | Download Client  |
| Sonarr         | 2         | Media Management |
| Radarr         | 2         | Media Management |
| Prowlarr       | 1         | Media Management |
| Seerr          | 1         | Requests         |
| Plex           | 1         | Playback         |
| Tautulli       | 1         | Playback         |
| Pi-hole        | 2         | DNS / Network    |
| Unbound        | 2         | DNS / Network    |
| Docker         | 1         | Container Runtime|

## Development Environment
- Primary development on Windows (no Docker required)
- Python 3.12 with venv for local development
- All provider URLs are configurable — no localhost assumptions
- Upstream APIs (Sonarr, Radarr, qBit, Pi-hole, etc.) accessed over LAN
  at their real addresses (e.g., 10.0.0.45:8989, 10.0.0.53:80)
- Docker provider is the exception: requires Docker socket, tested on Unraid only
- Provider tests must be runnable without Docker installed
- Development server: `uvicorn src.main:app --reload --port 8484`
- SQLite database file created locally in project directory during development

## Deployment Target
- Primary: Unraid Docker container
- Dockerfile included in project root
- Unraid Community Applications XML template included (see spec 18)
- Container mounts:
  - /var/run/docker.sock (for Docker provider)
  - /config (persistent data: SQLite DB, any local state)
- Environment variables:
  - SECRET_KEY (required)
  - PLEX_CLIENT_ID (required for Plex OAuth)
  - PUID / PGID (standard Unraid user mapping)
- App listens on port 8484 (container internal)
- Host port configurable via Unraid template (default: 8484)

## Spec Documents
```
specs/
  00-project.md            # This file — overview, stack, constraints
  01-architecture.md       # Provider system, caching, polling, HTMX patterns
  02-data-model.md         # SQLite schema, provider config, metrics abstraction
  03-provider-base.md      # BaseProvider contract, registration, permissions
  04-provider-qbit.md      # qBittorrent provider spec
  05-provider-arr-base.md  # ArrBaseProvider shared patterns (*arr family)
  06-provider-sonarr.md    # Sonarr provider spec (extends ArrBaseProvider)
  07-provider-radarr.md    # Radarr provider spec (extends ArrBaseProvider)
  08-provider-prowlarr.md  # Prowlarr provider spec (extends ArrBaseProvider)
  09-provider-seerr.md     # Seerr provider spec (Overseerr compatible)
  10-provider-plex.md      # Plex provider spec
  11-provider-tautulli.md  # Tautulli provider spec
  12-provider-pihole.md    # Pi-hole provider spec
  13-provider-unbound.md   # Unbound provider spec
  14-provider-docker.md    # Docker container runtime provider spec
  15-frontend.md           # HTMX patterns, layout, dark theme, components
  16-auth.md               # Plex OAuth + local auth, session management
  17-api-routes.md         # All backend API endpoints
  18-scenarios.md          # Test scenarios, edge cases, failure modes
  19-unraid-template.md    # Dockerfile + Unraid CA XML template spec
  20-hardening.md          # Security & operational hardening requirements
  21-conventions.md        # Code conventions, types, dependency versions, patterns
  22-build-plan.md         # Phased build sequence with context loading guide
  23-user-layout.md        # Per-user layout customization (sidebar groups, card ordering)
```

## UI Customization (Per-User Layout)
- Per-user layout preferences stored as JSON blob on User model
- Sidebar provider grouping: users create named groups, drag providers between groups
- Dashboard card reordering via drag-and-drop (SortableJS, vendored)
- Hide/show individual provider instances
- Layout merged with available instances on render (prunes deleted, appends new)
- Collapse/expand sidebar groups with persisted state
- CSS: modern dark theme with card hover glow, glassmorphism sidebar/header, status-colored effects
- API: 8 layout CRUD endpoints under /preferences/layout/*
- Spec: specs/23-user-layout.md

## Deferred (Post-MVP)
- Nginx Proxy Manager provider (cert expiry, proxy health)
- Grafana/InfluxDB provider (link to dashboards)
- Notifiarr integration
- autobrr / cross-seed / qbitmanage providers
- MetricsStore swap to InfluxDB
- Custom role creation UI
- Notification system (Discord, email on health changes)
- Provider SDK documentation for third-party development
