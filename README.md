# Great Eye

A self-hosted homelab operations dashboard that provides unified monitoring, health checking, and interactive management across multiple containerized services — all from a single pane of glass.

Built with **FastAPI**, **HTMX**, and **SQLite**. No JavaScript frameworks. No external databases. One container.

## Providers

| Provider | Category | Features |
|----------|----------|----------|
| **qBittorrent** | Download Client | Transfer stats, torrent management, pause/resume/delete |
| **Sonarr** | Media Management | Queue, calendar, missing episodes, search/refresh |
| **Radarr** | Media Management | Queue, calendar, movie search/refresh |
| **Prowlarr** | Media Management | Indexer health, stats, search sync |
| **Seerr** | Requests | Request queue, approval, media status (Overseerr compatible) |
| **Plex** | Playback | Libraries, active sessions, server info |
| **Tautulli** | Playback | Watch history, activity, recently added |
| **Pi-hole** | DNS / Network | Query stats, top domains/clients, enable/disable blocking |
| **Unbound** | DNS / Network | Resolver stats, cache management, DNSSEC status |
| **Docker** | Container Runtime | Container status, start/stop/restart, resource usage |

All providers support multiple instances (e.g., two qBittorrent servers, two Pi-holes).

## Features

- **Real-time dashboard** with SSE live updates (batch polling fallback)
- **Provider plugin architecture** — each integration is self-contained
- **Role-based access control** with granular, per-action permissions
- **Plex OAuth** + local username/password authentication
- **Admin UI** for provider CRUD, user/role management, and settings
- **Encrypted secrets** at rest (Fernet via HKDF key derivation)
- **Health monitoring** with failure tracking and recovery detection
- **Metrics collection** with configurable retention
- **Dark theme** with accessibility support (focus indicators, skip nav, reduced motion)
- **Unraid-ready** Docker deployment with Community Applications template

## Quick Start

### Docker (recommended)

```bash
docker run -d \
  --name greateye \
  -p 8484:8484 \
  -v /path/to/config:/config \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e SECRET_KEY=your-secret-key-here \
  -e PLEX_CLIENT_ID=your-plex-client-id \
  ghcr.io/greateye/greateye:latest
```

Generate a secret key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

On first launch, visit `http://localhost:8484` to complete the setup wizard and create your admin account.

### Unraid

Install via Community Applications using the included `greateye.xml` template. Configure `SECRET_KEY`, port, and `/config` path through the Unraid UI.

### Local Development

```bash
# Requires Python 3.12
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

pip install -r requirements.txt
cp .env.example .env
# Edit .env with your SECRET_KEY and PLEX_CLIENT_ID

uvicorn src.main:app --reload --port 8484
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Encryption key for secrets at rest + session signing |
| `PLEX_CLIENT_ID` | For Plex OAuth | Client ID for Plex authentication flow |
| `DATABASE_URL` | No | SQLite URL (default: `sqlite+aiosqlite:///config/greateye.db`) |
| `PUID` / `PGID` | No | User/group ID mapping for Docker (default: 1000/1000) |
| `TZ` | No | Timezone (default: `America/New_York`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

All provider configuration (URLs, API keys, polling intervals) is managed through the admin UI after setup.

## Architecture

```
src/
  main.py                 # FastAPI app, lifespan, middleware
  database.py             # SQLAlchemy async engine + session factory
  config.py               # pydantic-settings configuration
  auth/                   # Local auth, Plex OAuth, middleware, rate limiting
  models/                 # SQLAlchemy 2.0 ORM models (Mapped syntax)
  providers/              # Plugin system: base ABC, registry, scheduler, cache
    qbittorrent.py        #   10 provider implementations
    sonarr.py             #   ...
    ...
  routes/                 # FastAPI routers (dashboard, auth, admin, providers)
  services/               # Encryption, metrics, health, seed
  utils/                  # Validation, formatting, structured logging
templates/                # Jinja2 templates (pages, partials, cards, detail views)
static/                   # CSS (dark theme), HTMX, SSE extension
```

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Uvicorn
- **Frontend**: Jinja2 templates, HTMX, SSE — no build step
- **Database**: SQLite via aiosqlite
- **Auth**: bcrypt, Plex OAuth (PIN-based), signed session cookies
- **Encryption**: cryptography (Fernet + HKDF)
- **HTTP**: httpx (async)
- **Logging**: structlog (JSON, secret redaction)
- **Migrations**: Alembic
