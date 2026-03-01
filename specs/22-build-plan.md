# Build Plan

## Overview
This document sequences the implementation of Great Eye into phases.
Each phase produces working, testable code. Later phases build on
earlier ones. An implementing agent should complete phases in order.

Each phase lists the spec files to load for context. The agent should
read ONLY the listed specs for each phase to stay within context limits.

---

## Phase 0: Project Skeleton
**Goal:** Runnable application that starts, serves a page, and connects
to a database. No providers, no auth — just the bones.

**Load specs:** 21-conventions, 00-project

**Tasks:**
1. Create project directory structure per spec 21
2. Create `requirements.txt` with pinned dependencies
3. Create `src/config.py` (Settings from environment)
4. Create `src/database.py` (async engine, session factory, SQLite pragmas)
5. Create `src/main.py` (FastAPI app, lifespan startup/shutdown, static files, Jinja2 templates)
6. Create `alembic.ini` and `alembic/env.py` (async migration support)
7. Create `static/css/greateye.css` (dark theme base variables, typography, layout shell)
8. Create `static/js/` (copy HTMX and SSE extension)
9. Create `templates/base.html` (shell with sidebar placeholder, content block)
10. Create a minimal `GET /` route that renders the shell
11. Verify: `uvicorn src.main:app --reload --port 8484` starts and serves the page

**Outputs:** Running app skeleton, database connection, CSS theme foundation

**Tests:** App starts, GET / returns 200

---

## Phase 1: Data Model & Migrations
**Goal:** All database tables exist with proper schema. Seed data runs.

**Load specs:** 21-conventions, 02-data-model, 20-hardening (H1 only)

**Tasks:**
1. Create all ORM models in `src/models/`:
   - `user.py` (User)
   - `session.py` (Session)
   - `role.py` (Role, Permission, RolePermission)
   - `provider.py` (ProviderType, ProviderInstance, ProviderInstanceState, ProviderCache, ProviderActionLog)
   - `metrics.py` (Metric)
   - `auth.py` (PasswordResetToken, PlexApprovedUser)
2. Create `src/services/encryption.py` (Fernet key derivation via HKDF per H1)
3. Generate initial Alembic migration
4. Create seed data function: three system roles (admin, user, viewer)
5. Wire migration + seed into app startup lifespan

**Outputs:** Database schema, encryption service, seed roles

**Tests:** Migration runs on empty DB, seed creates 3 roles, encryption round-trips a value

---

## Phase 2: Auth System
**Goal:** Users can log in with username/password. Sessions work.
Admin can manage users. Setup wizard creates first admin.

**Load specs:** 21-conventions, 16-auth, 02-data-model (users/sessions sections), 17-api-routes (auth and setup sections)

**Tasks:**
1. Create `src/auth/local.py` (password hashing, verification)
2. Create `src/auth/middleware.py` (session validation middleware)
3. Create `src/auth/dependencies.py` (get_current_user, require_permission)
4. Create `src/routes/auth.py` (login, logout, change-password)
5. Create `src/routes/setup.py` (first-time setup wizard)
6. Create login page template (`templates/pages/login.html`)
7. Create setup wizard templates
8. Create setup middleware (redirect to /setup if no users)
9. Wire rate limiting (in-memory, login attempts)

**Outputs:** Working login flow, session management, setup wizard

**Tests:** Login success/failure, session creation/expiry, setup wizard creates admin, force reset flow

**Defer:** Plex OAuth (Phase 2b), password reset flow (Phase 2b)

---

## Phase 2b: Plex OAuth & Password Reset
**Goal:** Plex users can sign in. Password reset works.

**Load specs:** 16-auth (Plex OAuth section, password reset section), 17-api-routes (auth routes)

**Tasks:**
1. Create `src/auth/plex.py` (Plex OAuth flow, token exchange)
2. Add Plex OAuth routes to `src/routes/auth.py`
3. Create Plex approved users admin UI
4. Implement password reset token generation, admin queue, reset form
5. Implement account linking (local ↔ Plex)

**Outputs:** Full auth system with both methods

**Tests:** Plex OAuth flow (mocked), reset token lifecycle, account linking

---

## Phase 3: Provider Framework
**Goal:** BaseProvider contract implemented. Registry discovers, initializes,
and manages provider instances. Scheduler polls. Cache stores. Event bus
distributes. No individual providers yet — tested with a mock provider.

**Load specs:** 21-conventions, 03-provider-base, 01-architecture (caching, polling, event bus sections), 20-hardening (H3, H4, H5)

**Tasks:**
1. Create `src/providers/base.py` (BaseProvider ABC, all result dataclasses, ProviderMeta, PermissionDef)
2. Create `src/providers/registry.py` (auto-discovery, instance lifecycle, permission registration, action dispatch)
3. Create `src/providers/scheduler.py` (async polling with tiered intervals)
4. Create `src/providers/cache.py` (read/write to provider_cache table, TTL, stale detection)
5. Create `src/providers/event_bus.py` (in-memory pub/sub, per-connection Queues)
6. Create `src/utils/validation.py` (SSRF URL validation, action param validation)
7. Create `src/utils/formatting.py` (format_bytes, format_speed, format_eta, format_timestamp)
8. Create a `MockProvider` in tests that implements BaseProvider for testing the framework
9. Test the full lifecycle: register → initialize → poll → cache → event

**Outputs:** Complete provider infrastructure, tested with mock provider

**Tests:** Discovery finds providers, instance CRUD, scheduler polls at intervals, cache read/write/stale, event bus pub/sub, SSRF validation

---

## Phase 4: Dashboard & SSE
**Goal:** Dashboard page renders cards from cache. SSE pushes updates.
Batch polling works as fallback. No real providers yet — mock data.

**Load specs:** 21-conventions, 15-frontend, 01-architecture (SSE/batch sections), 17-api-routes (dashboard routes)

**Tasks:**
1. Create `src/routes/dashboard.py` (GET /dashboard, GET /dashboard/cards, GET /dashboard/stream)
2. Create `templates/pages/dashboard.html` (CSS grid, card slots)
3. Create a generic card template for the mock provider
4. Implement SSE endpoint (read from event bus Queues, send named events)
5. Implement batch polling endpoint (return all cards)
6. Implement delivery mode toggle (POST /preferences/delivery-mode)
7. Create toast notification partial
8. Wire permission filtering (only show instances user can view)

**Outputs:** Working dashboard with live updates from mock data

**Tests:** Dashboard renders, SSE sends events, batch returns all cards, mode toggle works

---

## Phase 5: First Real Provider — qBittorrent
**Goal:** qBittorrent provider fully implemented and visible on dashboard.
This validates the entire stack end-to-end with a real upstream API.

**Load specs:** 21-conventions, 04-provider-qbit, 03-provider-base

**Tasks:**
1. Create `src/providers/qbittorrent.py` (full implementation per spec 04)
2. Create `templates/cards/qbittorrent.html` (summary card)
3. Create `templates/detail/qbittorrent/` (overview, torrents tabs)
4. Create `src/routes/providers.py` (detail view route, action route) — generic, works for all providers
5. Create test fixtures from real qBittorrent API responses
6. Write provider tests (health, summary, detail, actions)
7. Test end-to-end: add qBit instance via DB, see it on dashboard

**Outputs:** First real provider working, detail view pattern established

**Tests:** Provider unit tests, card renders, detail view loads, actions execute

---

## Phase 6: ArrBaseProvider + Sonarr + Radarr
**Goal:** Arr family providers implemented. Shared base class proven.

**Load specs:** 21-conventions, 05-provider-arr-base, 06-provider-sonarr, 07-provider-radarr

**Tasks:**
1. Create `src/providers/arr_base.py` (ArrBaseProvider with shared health, queue, command, disk space, validate_config)
2. Create `src/providers/sonarr.py` (extends ArrBaseProvider)
3. Create `src/providers/radarr.py` (extends ArrBaseProvider)
4. Create card and detail templates for both
5. Create test fixtures for both
6. Write tests for shared base + each provider

**Outputs:** Three providers working (qBit + Sonarr + Radarr)

**Tests:** ArrBaseProvider health check, queue normalization, command execution. Sonarr and Radarr specific summary/detail shapes.

---

## Phase 7: Remaining Providers — Batch 1
**Goal:** Prowlarr, Seerr, Plex, Tautulli implemented.

**Load specs:** 21-conventions, 08-provider-prowlarr, 09-provider-seerr, 10-provider-plex, 11-provider-tautulli

**Tasks:**
1. Create `src/providers/prowlarr.py` (extends ArrBaseProvider with v1 override)
2. Create `src/providers/seerr.py` (standalone BaseProvider)
3. Create `src/providers/plex.py` (standalone BaseProvider, JSON Accept header)
4. Create `src/providers/tautulli.py` (standalone BaseProvider, cmd pattern)
5. Card and detail templates for each
6. Test fixtures and tests for each

**Outputs:** Seven providers total

**Tests:** Per-provider health, summary, detail, actions

---

## Phase 8: Remaining Providers — Batch 2
**Goal:** Pi-hole, Unbound, Docker implemented. All providers complete.

**Load specs:** 21-conventions, 12-provider-pihole, 13-provider-unbound, 14-provider-docker, 20-hardening (H2)

**Tasks:**
1. Create `src/providers/pihole.py` (session auth, v6 API)
2. Create `src/providers/unbound.py` (TLS control socket or HTTP sidecar)
3. Create `src/providers/docker.py` (Unix socket, env stripping per H2, self-protection)
4. Card and detail templates for each
5. Test fixtures and tests for each

**Outputs:** All 10 provider types implemented

**Tests:** Per-provider tests, Docker self-protection, Pi-hole session refresh

---

## Phase 9: Admin UI
**Goal:** Admin can configure providers, manage users, manage roles
through the web UI.

**Load specs:** 21-conventions, 17-api-routes (admin routes), 15-frontend (admin pages section), 16-auth (role management)

**Tasks:**
1. Create `src/routes/admin.py` (all admin routes)
2. Create `templates/admin/providers.html` (instance list)
3. Create `templates/admin/provider_form.html` (add/edit, dynamic from config_schema)
4. Create `templates/admin/users.html` (user management)
5. Create `templates/admin/roles.html` (role/permission matrix)
6. Create `templates/admin/settings.html` (system settings)
7. Implement "Test Connection" (calls validate_config via HTMX)
8. Implement dashboard sort order (drag and drop or simple up/down)
9. Implement password reset queue view

**Outputs:** Complete admin interface

**Tests:** CRUD operations for providers, users, roles. Permission changes propagate correctly.

---

## Phase 10: Metrics & Self-Health
**Goal:** Time-series metrics collected and available. Self-health
endpoint works.

**Load specs:** 21-conventions, 01-architecture (MetricsStore section), 20-hardening (H6, H7, H8)

**Tasks:**
1. Create `src/services/metrics.py` (MetricsStore SQLite implementation)
2. Wire metrics recording into scheduler (after each poll, write metrics)
3. Create retention cleanup job (daily, delete old metrics)
4. Create `src/services/health.py` (self-health logic)
5. Add `GET /health` route (unauthenticated)
6. Configure structlog with JSON formatter, context binding, secret redaction
7. Wire structured logging throughout existing code

**Outputs:** Metrics being collected, /health endpoint, structured logs

**Tests:** Metrics write/query/cleanup, health endpoint response shape, log output format

---

## Phase 11: Deployment
**Goal:** Docker image builds, runs, and works on Unraid.

**Load specs:** 19-unraid-template

**Tasks:**
1. Create `Dockerfile`
2. Create `docker-entrypoint.sh` (PUID/PGID, migrations, startup)
3. Create `greateye.xml` (Unraid CA template)
4. Test build locally
5. Test container startup with required env vars
6. Test volume mounts (/config persistence, Docker socket)
7. Document: how to get SECRET_KEY, how to get PLEX_CLIENT_ID

**Outputs:** Deployable Docker image, Unraid template

**Tests:** Container starts, serves dashboard, persists data across restart

---

## Phase 12: Polish & Edge Cases
**Goal:** Handle the scenarios from spec 18 that aren't covered by
individual phase tests.

**Load specs:** 18-scenarios

**Tasks:**
1. Review all 40 scenarios, identify any not covered by existing tests
2. Implement missing error handling (partial data, stale display, etc.)
3. Ensure setup wizard → first provider → dashboard flow is smooth
4. Test multi-user concurrent access
5. Test session expiry during SSE
6. Responsive CSS testing (mobile, tablet, desktop)
7. Accessibility pass: keyboard navigation, focus indicators, ARIA labels

**Outputs:** Production-ready application

---

## Dependency Graph

```
Phase 0  (skeleton)
  │
Phase 1  (data model)
  │
Phase 2  (auth) ──── Phase 2b (Plex OAuth, reset)
  │
Phase 3  (provider framework)
  │
Phase 4  (dashboard + SSE)
  │
Phase 5  (qBittorrent — first real provider)
  │
Phase 6  (ArrBase + Sonarr + Radarr)
  │
Phase 7  (Prowlarr, Seerr, Plex, Tautulli)
  │
Phase 8  (Pi-hole, Unbound, Docker)
  │
Phase 9  (admin UI)
  │
Phase 10 (metrics + health + logging)
  │
Phase 11 (Docker deployment)
  │
Phase 12 (polish + edge cases)
```

Phases 0–4 are strictly sequential — each builds on the previous.
Phases 5–8 are sequential in practice (each adds providers) but
an individual provider could be done in any order.
Phase 9 can start after Phase 5 (needs at least one real provider).
Phase 10 can start after Phase 4.
Phase 11 can start after Phase 9.
Phase 12 is last.

## Context Loading Guide

For each phase, the agent should load:
1. This build plan (for phase-specific tasks)
2. The conventions spec (21) — always loaded
3. The phase-specific specs listed above

Maximum specs per phase: 4-5 files. This keeps the agent well within
context limits while having full information for the current task.

The agent should NOT load all 23 spec files at once. That wastes
context and increases the chance of the agent conflating patterns
from different providers or systems.
