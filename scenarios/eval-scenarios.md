# Agent Evaluation Scenarios

## Purpose
This document is a private evaluation framework for assessing Claude
Code's implementation of the Great Eye specs. The agent never sees
this document. It receives only the specs (00–22) and the build plan.

These scenarios test whether the agent faithfully implements what
the specs say versus falling back to training priors, skipping
details, or making assumptions. Run the relevant scenarios after
each build phase before proceeding.

## Scoring

Each scenario has a verdict:

- **PASS**: Implemented correctly per spec
- **PARTIAL**: Partially correct, minor deviations
- **FAIL**: Missing, wrong, or contradicts spec
- **N/A**: Not yet applicable (depends on later phase)

Track results in a simple table per phase. Patterns of PARTIAL/FAIL
across phases reveal systemic issues (e.g., "agent consistently
ignores error handling" or "agent prefers its own patterns over spec").

---

## Phase 0: Project Skeleton

### S0.1 — Directory Structure
Check: Does the project tree match spec 21 exactly?
Look for:
- `src/` not `app/` or `greateye/`
- `src/providers/` not `src/provider/` (plural)
- `src/models/` with separate files per domain (not one models.py)
- `src/schemas/` exists and is separate from models
- `tests/fixtures/` directory exists
- `static/css/`, `static/js/`, `static/icons/` structure

Common drift: Agent creates a flat structure or uses its own preferred layout.

### S0.2 — Dependency Versions
Check: Does `requirements.txt` pin the versions from spec 21?
Look for:
- SQLAlchemy 2.0.x (not 1.4.x)
- httpx (not requests, not aiohttp)
- structlog (not standard logging configured as JSON)
- aiosqlite (required for async SQLAlchemy with SQLite)
- pydantic-settings (not python-dotenv for config)

Common drift: Agent uses `requests` or `aiohttp` from training priors.

### S0.3 — SQLite Pragmas
Check: Does database initialization set the three required pragmas?
Look for in `src/database.py` or startup:
- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`
- `PRAGMA busy_timeout=5000`

Common drift: Agent skips pragmas entirely or only sets WAL.

### S0.4 — Settings Pattern
Check: Does config use pydantic-settings BaseSettings?
Look for:
- `src/config.py` with a Settings class
- `secret_key` as required field (no default)
- `database_url` with async SQLite default (`sqlite+aiosqlite:///...`)
- Singleton pattern (`settings = Settings()` at module level)

Common drift: Agent uses python-dotenv with manual os.environ lookups.

### S0.5 — Dark Theme CSS Variables
Check: Does the CSS define the color palette from spec 15?
Look for:
- `--bg-primary: #0f1117` (not a lighter shade)
- Health status colors defined as CSS variables
- System font stack (not importing Google Fonts)
- Monospace font for technical values

Common drift: Agent uses Tailwind, imports a CSS framework, or picks
its own color scheme.

---

## Phase 1: Data Model

### S1.1 — ORM Model Style
Check: Do models use SQLAlchemy 2.0 Mapped annotations?
Look for:
- `from sqlalchemy.orm import Mapped, mapped_column`
- `id: Mapped[int] = mapped_column(primary_key=True)`
- NOT `id = Column(Integer, primary_key=True)` (1.x style)

Common drift: Agent defaults to Column() syntax from training data
prevalence. This is the single most likely convention violation.

### S1.2 — Fernet Key Derivation
Check: Does encryption use HKDF, not raw SECRET_KEY?
Look for in `src/services/encryption.py`:
- Import from `cryptography.hazmat.primitives.kdf.hkdf`
- Static salt `b"greateye-fernet-v1"`
- Info parameter `b"provider-config-encryption"`
- NOT `Fernet(base64.urlsafe_b64encode(secret_key.encode()))`

Common drift: Agent passes SECRET_KEY directly to Fernet or uses
a simple SHA-256 hash. This is a spec 20 H1 requirement.

### S1.3 — Table Schema Match
Check: Do the tables match spec 02?
Spot-check these specific fields:
- `provider_instances.config_encrypted` (TEXT, Fernet-encrypted JSON)
- `provider_cache.tier` (TEXT: "health", "summary", "detail")
- `provider_cache.stale_after` (DATETIME, separate from updated_at)
- `sessions.delivery_mode` (TEXT, default "sse")
- `users.auth_method` (TEXT: "local", "plex", "both")
- `metrics.tags` (TEXT, JSON-encoded)

Common drift: Agent invents its own schema or adds/removes fields.

### S1.4 — Seed Roles
Check: Are exactly three roles seeded with correct names?
Look for: admin, user, viewer (not Admin/User/Viewer, not
moderator, not editor).

---

## Phase 2: Auth System

### S2.1 — Password Hashing
Check: Uses bcrypt with cost factor 12.
Look for:
- `bcrypt.hashpw()` with `bcrypt.gensalt(rounds=12)`
- NOT argon2, NOT scrypt, NOT pbkdf2
- Timing-safe comparison (bcrypt.checkpw handles this)

### S2.2 — Session Cookie Attributes
Check: Cookie has all required security attributes.
Look for in session creation:
- `HttpOnly` flag
- `SameSite=Lax` (not Strict, not None)
- `Secure` conditional on HTTPS detection
- `Max-Age` set (not using session cookies that die with browser)
- Cookie name is `session_id`

Common drift: Agent uses JWT tokens instead of server-side sessions.
This is a critical spec violation — the specs explicitly use
database-backed sessions.

### S2.3 — Login Error Messages
Check: Failed login returns generic message.
Look for:
- Same error for bad username and bad password
- "Invalid credentials" or similar (not "User not found" or
  "Incorrect password")
- No information leakage about which field was wrong

### S2.4 — Setup Wizard Guard
Check: /setup is only accessible when no users exist.
Look for:
- Middleware or dependency that checks user count
- Returns 404 (not 403) when users exist
- All other routes redirect to /setup when no users exist

### S2.5 — Session Invalidation on Password Change
Check: Changing password kills ALL sessions for that user.
Look for:
- DELETE from sessions WHERE user_id = ...
- Not just the current session — all of them
- New session created after password change

---

## Phase 3: Provider Framework

### S3.1 — Result Types Are Dataclasses
Check: HealthResult, SummaryResult, etc. are dataclasses.
Look for:
- `from dataclasses import dataclass`
- `@dataclass` decorator
- NOT Pydantic BaseModel (spec 21 is explicit: dataclasses for
  provider results, Pydantic for request validation only)

Common drift: Agent uses Pydantic for everything because it's
already imported for FastAPI.

### S3.2 — HealthStatus Enum Values
Check: Enum uses lowercase string values.
Look for:
- `UP = "up"` not `UP = "UP"` or `UP = 1`
- Inherits from `str, Enum` (for JSON serialization)
- Four values: up, degraded, down, unknown

### S3.3 — Provider Auto-Discovery
Check: Registry discovers providers by scanning the providers module.
Look for:
- Some mechanism to find all BaseProvider subclasses
- NOT a hardcoded list of imports
- Each provider registers via its meta() classmethod

### S3.4 — SSRF Validation
Check: Provider URL validation blocks metadata endpoints.
Look for in `src/utils/validation.py`:
- `169.254.169.254` blocked
- `metadata.google.internal` blocked
- Loopback (127.0.0.0/8) blocked
- Private LAN ranges (10.x, 192.168.x) explicitly ALLOWED
- Validation runs in validate_config, not just in admin form

### S3.5 — Event Bus Pattern
Check: Uses asyncio.Queue per connection, not direct coupling.
Look for:
- Event bus maintains a set/dict of subscriber Queues
- Publishing puts events into all subscriber Queues
- Subscribe returns a Queue, unsubscribe removes it
- NOT using SSE library that manages its own subscribers

### S3.6 — HTTP Client Ownership
Check: Registry creates httpx.AsyncClient, not the provider.
Look for:
- Client created in registry during instance initialization
- Client injected into provider (provider.http_client = client)
- Provider never calls `httpx.AsyncClient()` itself
- Client closed in registry during instance cleanup

---

## Phase 4: Dashboard & SSE

### S4.1 — SSE Event Naming
Check: Events use the naming convention from spec 15.
Look for:
- `event: summary:{instance_id}` (not `event: update` or generic)
- `event: health:{instance_id}` (separate from summary)
- Instance ID in the event name (enables targeted HTMX swap)

### S4.2 — Full State on SSE Reconnect
Check: Reconnecting client gets all current summaries.
Look for:
- On new SSE connection, iterate all cached summaries
- Send each as a named event
- NOT relying on the client to still have previous state

### S4.3 — HX-Request Detection
Check: Routes serve full page vs partial based on header.
Look for:
- `request.headers.get("HX-Request")` check
- Full page: renders base.html with content
- HTMX request: renders just the content partial
- This pattern is on EVERY route (not just dashboard)

### S4.4 — Permission Filtering on Dashboard
Check: Dashboard only shows instances the user can view.
Look for:
- Card rendering checks `{type}.view` permission
- SSE stream only sends events for permitted instances
- Batch endpoint only returns permitted cards

---

## Phase 5: qBittorrent Provider

### S5.1 — Cookie-Based Auth
Check: Uses qBit's session cookie pattern, not basic auth.
Look for:
- POST to `/api/v2/auth/login` with username/password
- SID cookie extracted and stored
- Cookie sent on subsequent requests
- Re-auth on 403 response

### S5.2 — v4/v5 Compatibility
Check: Provider handles state name differences.
Look for:
- Version detection from `/api/v2/app/version`
- v4: "pausedDL"/"pausedUP", v5: "stoppedDL"/"stoppedUP"
- v4: /torrents/pause endpoint, v5: /torrents/stop endpoint
- Version cached, not re-checked every request

Common drift: Agent only implements v5 or only v4, not both.

### S5.3 — Category Mapping
Check: Torrents tagged with purpose (sonarr/radarr/prowlarr).
Look for:
- Category/tag field used to identify torrent source
- Summary groups downloads by category
- NOT just showing raw torrent names

---

## Phase 6: Arr Providers

### S6.1 — ArrBaseProvider Inheritance
Check: Sonarr and Radarr extend ArrBaseProvider, not BaseProvider.
Look for:
- `class SonarrProvider(ArrBaseProvider)`
- Shared methods (health_check, validate_config, _fetch_queue,
  _execute_command) live in arr_base.py
- NOT duplicated in sonarr.py and radarr.py

### S6.2 — API Version Override
Check: Prowlarr uses v1, Sonarr/Radarr use v3.
Look for:
- `api_version` property on ArrBaseProvider (default "v3")
- Prowlarr overrides to return "v1"
- URL construction uses `{self.api_base}` not hardcoded `/api/v3/`

Common drift: Agent hardcodes /api/v3/ in ArrBaseProvider methods.

### S6.3 — Queue Normalization
Check: Each arr provider normalizes queue records to a common shape.
Look for:
- `_normalize_queue_record()` abstract method on ArrBaseProvider
- Sonarr implementation maps series/episode info
- Radarr implementation maps movie info
- Common fields: title, progress, status, size, time_left

### S6.4 — Sonarr Missing Episodes Endpoint
Check: Uses /api/v3/wanted/missing (not computing from /series).
Look for:
- Dedicated API call to `/api/v3/wanted/missing`
- Pagination params (page, pageSize, sortKey)
- NOT iterating all episodes and filtering unmonitored

### S6.5 — Radarr No Dedicated Missing Endpoint
Check: Radarr uses /api/v3/movie with filtering.
Look for:
- Movie list filtered by `monitored=true` and `hasFile=false`
  (or equivalent)
- NOT calling a `/api/v3/wanted/missing` endpoint on Radarr
  (it doesn't exist with the same behavior as Sonarr's)

---

## Phase 7: Batch 1 Providers

### S7.1 — Prowlarr Derived Status
Check: Indexer status is computed, not from a single API field.
Look for:
- Combination of /indexerstats and /health endpoints
- Status derivation logic: healthy/degraded/failing/disabled
- NOT assuming a `status` field exists on the indexer object

### S7.2 — Seerr App Detection
Check: Provider identifies Overseerr vs Seerr from API response.
Look for:
- Check `/api/v1/status` response for app name
- Log which variant was detected
- Both use identical API — no code branching by app name
- validate_config includes migration note if Overseerr detected

### S7.3 — Plex XML vs JSON Handling
Check: Provider requests JSON, handles XML fallback.
Look for:
- `Accept: application/json` header on requests
- Health check uses unauthenticated `/identity` first
- Then authenticated `/` to verify token
- NOT parsing XML as the primary path

### S7.4 — Tautulli Single-Endpoint Pattern
Check: All API calls go through /api/v2 with cmd parameter.
Look for:
- Single base URL `/api/v2`
- `cmd` query parameter varies per call
- `apikey` as query parameter (not header)
- NOT using different URL paths for different data

---

## Phase 8: Batch 2 Providers

### S8.1 — Pi-hole v6 Session Auth
Check: Uses POST password → SID cookie pattern.
Look for:
- POST to `/api/auth` with password
- SID cookie/token extracted from response
- Session refresh on expiry
- NOT using the v5 `?auth=token` query parameter pattern

Common drift: Agent implements v5 API (much more training data
available for Pi-hole v5 than v6).

### S8.2 — Pi-hole v5 Detection
Check: validate_config detects v5 and returns useful error.
Look for:
- Probes for v6 API first
- If v5 detected (e.g., /admin/api.php responds), returns
  validation error with upgrade message
- Does NOT silently fall back to v5 API

### S8.3 — Unbound Control Interface
Check: Uses unbound-control over TLS, not HTTP.
Look for:
- TLS connection to port 8953 (default)
- Config requires cert paths (server_cert, control_key, control_cert)
- `stats_noreset` command (not `stats` which resets counters)
- OR HTTP sidecar fallback documented as alternative

Common drift: Agent assumes Unbound has an HTTP API and invents
REST endpoints. This is the most unusual provider in the stack.

### S8.4 — Docker Env Stripping
Check: Environment variables never reach templates or cache.
Look for:
- Container normalization drops `Env` field
- Detail view does NOT show environment variables
- NOT even in a collapsed/hidden section
- Volume mount host paths also stripped

This is spec 20 H2 — a security requirement.

### S8.5 — Docker Self-Protection
Check: Provider excludes its own container from actions.
Look for:
- Own container ID detection on startup
- Action handlers reject requests targeting own container
- Container list may include self (for display) but actions blocked
- Detection method: read /proc/self/cgroup or HOSTNAME env var

---

## Phase 9: Admin UI

### S9.1 — Dynamic Config Forms
Check: Provider config form is generated from config_schema.
Look for:
- config_schema JSON drives form field rendering
- Secret fields render as password inputs
- "Test Connection" button triggers validate_config via HTMX
- NOT hardcoded forms per provider type

### S9.2 — Permission Matrix
Check: Role editing shows a permission grid.
Look for:
- Matrix of permissions × roles
- Permissions grouped by provider and category
- Toggle individual permissions
- System roles (admin/user/viewer) cannot be deleted

### S9.3 — Test Connection Response
Check: Returns inline HTML result, not page redirect.
Look for:
- HTMX POST to test endpoint
- Success: shows app name and version
- Failure: shows specific error (bad key, unreachable, wrong app)
- Result appears inline in the form, not as a toast

---

## Phase 10: Metrics & Health

### S10.1 — Self-Health Endpoint
Check: GET /health returns the shape from spec 20 H6.
Look for:
- No authentication required
- Returns JSON (not HTML — this is the one JSON endpoint)
- Includes: status, version, database, scheduler, provider counts
- Does NOT expose provider names, URLs, or config
- Returns 503 if critically degraded

### S10.2 — Structured Logging
Check: Uses structlog with context binding.
Look for:
- `structlog.get_logger()` at module level
- `.bind()` for adding context (instance_id, user_id)
- JSON output format configured for production
- Secret redaction filter (API keys, tokens never logged)

Common drift: Agent uses standard logging with string formatting.

### S10.3 — Metrics Retention
Check: Daily cleanup job deletes old metrics.
Look for:
- Configurable retention (default 30 days)
- Batched deletes (not one giant DELETE)
- Runs on schedule (not on every request)
- WAL mode prevents read blocking during cleanup

---

## Phase 11: Deployment

### S11.1 — Dockerfile Base Image
Check: Uses python:3.12-slim.
Look for:
- `FROM python:3.12-slim` (not alpine, not full, not 3.11)
- Multi-stage build is acceptable but not required
- PUID/PGID handling in entrypoint
- Non-root user created and used

### S11.2 — Unraid CA Template
Check: XML template has all required config entries.
Look for:
- Port 8484 mapping
- /config volume mapping to /mnt/user/appdata/greateye
- Docker socket mapping (optional, default ro)
- SECRET_KEY as masked variable
- PLEX_CLIENT_ID as optional variable
- PUID/PGID with Unraid defaults (99/100)
- WebUI URL set correctly

### S11.3 — Entrypoint Migration
Check: Database migrations run before app starts.
Look for:
- `alembic upgrade head` in entrypoint
- Runs as greateye user (not root)
- Runs BEFORE uvicorn starts
- Failure in migration prevents app startup

---

## Cross-Phase Checks (Run Anytime)

### CX.1 — No requests Library
Check: httpx is used for ALL HTTP calls.
Look for: `import requests` anywhere in src/ — should not exist.

### CX.2 — No Bare Exceptions
Check: All try/except blocks catch specific exceptions.
Look for: `except:` or `except Exception:` without re-raising or
logging. Bare exception swallowing hides bugs.

### CX.3 — Async All the Way
Check: No synchronous I/O in async functions.
Look for:
- No `open()` (use `aiofiles` if needed)
- No `time.sleep()` (use `asyncio.sleep()`)
- No synchronous httpx calls (use `await client.get()`)
- No `session.execute()` without `await`

### CX.4 — Type Hints Present
Check: All function signatures have type hints.
Look for: Functions missing return type or parameter types.
Spec 21 requires type hints on ALL signatures.

### CX.5 — Double Quotes
Check: String literals use double quotes.
This is minor but tests whether the agent reads and follows
the conventions spec at all. If it uses single quotes throughout,
it may not be reading spec 21 carefully.

### CX.6 — Import Organization
Check: Imports follow stdlib → third-party → local ordering.
Look for: Mixed import groups, especially local imports between
third-party imports.

### CX.7 — No JWT
Check: Auth uses server-side sessions, not JWT tokens.
Look for: Any import of `jwt`, `python-jose`, `PyJWT`, or
`fastapi-jwt-auth`. The specs explicitly use database-backed
sessions with cookies.

Common drift: Agent defaults to JWT because it's the most common
FastAPI auth pattern in training data.

---

## Meta-Evaluation: Agent Behavior Patterns

Track these across all phases to identify systemic issues:

### M1 — Spec Reading Depth
Does the agent read the full spec or skim for high-level structure?
Indicator: catches specific details like "cost factor 12" for bcrypt,
"stats_noreset" for Unbound, "stoppedDL" vs "pausedDL" for qBit v5.

### M2 — Training Prior Override
Does the agent follow the spec when it conflicts with common patterns?
Key conflicts to watch:
- SQLAlchemy 2.0 Mapped vs 1.x Column (spec says 2.0)
- Dataclasses vs Pydantic for provider results (spec says dataclasses)
- httpx vs requests (spec says httpx)
- Server sessions vs JWT (spec says sessions)
- structlog vs standard logging (spec says structlog)

### M3 — Convention Consistency
Does the agent maintain conventions across files or drift over time?
Check: First provider follows conventions. Fifth provider still does.
Context window pressure may cause later code to diverge.

### M4 — Error Handling Completeness
Does the agent implement error paths or just happy paths?
Check: Provider health_check handles 401, 403, timeout, connection
refused, unexpected response format — not just 200 OK.

### M5 — Security Requirement Adherence
Does the agent implement security requirements without being reminded?
Check: Docker env stripping, SSRF validation, secret redaction in
logs, session invalidation on password change. These are scattered
across specs — the agent must synthesize them.

### M6 — Deferred vs Invented
When the spec says "defer to Phase X" or "not in v1", does the
agent respect that or implement it anyway?
Check: Plex OAuth should not appear until Phase 2b. Email-based
password reset should not appear (spec says admin-mediated only).
