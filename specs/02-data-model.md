# Data Model

## Overview
All persistent state is stored in a single SQLite database at the path
specified by DATABASE_URL (default: sqlite:///data/greateye.db). The schema
is managed by SQLAlchemy with Alembic migrations for version upgrades.

The database serves four purposes:
1. **Auth & Users** — accounts, sessions, roles, permissions
2. **Provider Config** — instance definitions, encrypted credentials
3. **Cache** — polled provider data for fast frontend rendering
4. **Metrics** — time-series data behind the MetricsStore abstraction

All timestamps are stored as UTC datetime. All encrypted fields use Fernet
symmetric encryption keyed from the SECRET_KEY environment variable.

## Entity Relationship Diagram
```
users ─────────────┐
  │                │
  │ has many       │ has one
  ▼                ▼
sessions       user_roles ──► roles
                               │
                               │ has many
                               ▼
                          role_permissions ──► permissions
                                                  ▲
                                                  │ registers
                                                  │
provider_types ◄── registers ── provider code     │
  │                                               │
  │ has many                                      │
  ▼                                               │
provider_instances ─── registers ─────────────────┘
  │
  │ has many
  ├──► provider_cache
  ├──► provider_action_log
  └──► metrics (via MetricsStore)

password_reset_tokens ──► users
```

## Tables

### users
Stores all user accounts. A single account can have both local and Plex
auth methods linked, allowing the user to log in via either.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| username           | String(100)  | UNIQUE, NOT NULL               | Display name / login name      |
| auth_method        | String(20)   | NOT NULL                       | "local", "plex", or "both"    |
| password_hash      | String(255)  | NULLABLE                       | bcrypt hash (local/both auth)  |
| plex_token         | Text         | NULLABLE                       | Fernet encrypted (plex/both)   |
| plex_user_id       | String(100)  | NULLABLE, UNIQUE               | Plex account identifier        |
| email              | String(255)  | NULLABLE                       | Optional, for future email     |
| role_id            | Integer      | FK → roles.id, NOT NULL        |                                |
| force_reset        | Boolean      | NOT NULL, default False        | Force password change on login |
| is_active          | Boolean      | NOT NULL, default True         | Soft disable without deletion  |
| created_at         | DateTime     | NOT NULL, default utcnow       |                                |
| updated_at         | DateTime     | NOT NULL, auto-update          |                                |

Indexes: username, plex_user_id, role_id

**Account linking (TODO — v1 implementation):**
- A local-only user can link their Plex account from a settings page,
  changing auth_method from "local" to "both"
- A Plex-only user can set a local password from a settings page,
  changing auth_method from "plex" to "both"
- Admin can link/unlink auth methods for any user from user management
- Login page accepts either method — if a Plex OAuth resolves to a user
  with auth_method "both", the Plex token is refreshed on that account
- If Plex is unreachable, users with "both" can still log in with password

### sessions
Active user sessions. Cleaned up periodically by the scheduler.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | String(64)   | PK                             | Secure random token            |
| user_id            | Integer      | FK → users.id, NOT NULL        |                                |
| created_at         | DateTime     | NOT NULL, default utcnow       |                                |
| expires_at         | DateTime     | NOT NULL                       | Default: created_at + 24h      |
| delivery_mode      | String(10)   | NOT NULL, default "sse"        | "sse" or "batch"              |

Indexes: user_id, expires_at

### roles
Named bundles of permissions. Three seeded on first run.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| name               | String(50)   | UNIQUE, NOT NULL               | "admin", "user", "viewer"     |
| description        | String(255)  | NULLABLE                       |                                |
| is_system          | Boolean      | NOT NULL, default False        | True for the three defaults    |
| created_at         | DateTime     | NOT NULL, default utcnow       |                                |

System roles (is_system=True) cannot be deleted but can have their
permissions modified. This prevents accidental removal of the admin role
while still allowing customization.

### permissions
Individual permission definitions. Registered by provider code on startup.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| key                | String(100)  | UNIQUE, NOT NULL               | e.g., "sonarr.search"        |
| display_name       | String(100)  | NOT NULL                       | e.g., "Search for episodes"   |
| description        | String(255)  | NULLABLE                       | Human-readable explanation     |
| provider_type      | String(50)   | NOT NULL                       | Which provider owns this       |
| category           | String(50)   | NOT NULL                       | "read", "action", "admin"     |

The category field groups permissions for display in the admin UI:
- **read**: viewing data (dashboards, details)
- **action**: triggering operations (search, import, pause)
- **admin**: managing configuration (add/remove instances, restart containers)

Indexes: key, provider_type

### role_permissions
Many-to-many mapping between roles and permissions.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| role_id            | Integer      | FK → roles.id, PK             |                                |
| permission_id      | Integer      | FK → permissions.id, PK       |                                |

Composite primary key (role_id, permission_id).

### password_reset_tokens
Time-limited tokens for password reset flow.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| user_id            | Integer      | FK → users.id, NOT NULL        |                                |
| token_hash         | String(255)  | UNIQUE, NOT NULL               | SHA-256 hash of the token      |
| created_at         | DateTime     | NOT NULL, default utcnow       |                                |
| expires_at         | DateTime     | NOT NULL                       | Default: created_at + 1h       |
| used               | Boolean      | NOT NULL, default False        |                                |

The plaintext token is shown to the admin (or emailed in the future).
Only the hash is stored. Token is single-use — marked used after consumption.

Indexes: token_hash, user_id, expires_at

### plex_approved_users
Whitelist of Plex usernames approved by admin for OAuth access.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| plex_username      | String(100)  | UNIQUE, NOT NULL               | Plex username to allow         |
| default_role_id    | Integer      | FK → roles.id, NOT NULL        | Role assigned on first login   |
| approved_by        | Integer      | FK → users.id, NOT NULL        | Admin who approved             |
| created_at         | DateTime     | NOT NULL, default utcnow       |                                |

When a Plex user authenticates via OAuth, their username is checked against
this table. If found, a user record is created (or linked) with the
specified role. If not found, login is denied.

### provider_types
Registered provider type metadata. Populated on startup by auto-discovery.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | String(50)   | PK                             | e.g., "qbittorrent", "sonarr" |
| display_name       | String(100)  | NOT NULL                       | e.g., "qBittorrent"          |
| icon               | String(100)  | NULLABLE                       | Icon class or filename         |
| category           | String(50)   | NOT NULL                       | "download_client", "media",    |
|                    |              |                                | "dns", "playback", "runtime"  |
| config_schema      | Text (JSON)  | NOT NULL                       | Required config fields as JSON |
| default_intervals  | Text (JSON)  | NOT NULL                       | Default polling intervals      |

config_schema example:
```json
{
  "fields": [
    {"key": "url", "label": "URL", "type": "url", "required": true},
    {"key": "api_key", "label": "API Key", "type": "secret", "required": true},
    {"key": "username", "label": "Username", "type": "string", "required": false},
    {"key": "password", "label": "Password", "type": "secret", "required": false}
  ]
}
```

default_intervals example:
```json
{
  "health_seconds": 30,
  "summary_seconds": 60,
  "detail_cache_seconds": 300
}
```

This table is wiped and repopulated on each startup from provider class
metadata. It is a runtime reflection of what provider code is installed,
not user-editable config.

### provider_instances
User-configured instances of providers. This is where the actual connections
to upstream services are defined.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| provider_type_id   | String(50)   | FK → provider_types.id, NOT NULL |                              |
| display_name       | String(100)  | NOT NULL                       | e.g., "Sonarr (4K)"          |
| config             | Text (JSON)  | NOT NULL                       | Encrypted secrets in values    |
| health_interval    | Integer      | NOT NULL                       | Seconds between health polls   |
| summary_interval   | Integer      | NOT NULL                       | Seconds between summary polls  |
| detail_cache_ttl   | Integer      | NOT NULL                       | Seconds to cache detail data   |
| is_enabled         | Boolean      | NOT NULL, default True         |                                |
| sort_order         | Integer      | NOT NULL, default 0            | Dashboard display order        |
| created_at         | DateTime     | NOT NULL, default utcnow       |                                |
| updated_at         | DateTime     | NOT NULL, auto-update          |                                |

The config column stores a JSON object matching the provider type's
config_schema. Fields marked as type "secret" in the schema are
Fernet-encrypted before storage. Example stored value:
```json
{
  "url": "http://10.0.0.45:8989",
  "api_key": "gAAAAABl... (Fernet ciphertext)"
}
```

Indexes: provider_type_id, is_enabled

### provider_instance_state
Runtime health state for each instance. Updated by the scheduler.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| instance_id        | Integer      | FK → provider_instances.id, PK |                                |
| health_status      | String(20)   | NOT NULL, default "unknown"    | "up", "degraded", "down",     |
|                    |              |                                | "unknown", "disabled"         |
| health_message     | String(255)  | NULLABLE                       | Human-readable status detail   |
| last_health_check  | DateTime     | NULLABLE                       | When health was last polled    |
| last_successful    | DateTime     | NULLABLE                       | When last check succeeded      |
| failure_count      | Integer      | NOT NULL, default 0            | Consecutive failures           |
| updated_at         | DateTime     | NOT NULL, auto-update          |                                |

Separated from provider_instances to allow frequent updates without
write contention on the config table.

### provider_cache
Cached data from provider polling. One row per instance per tier.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| instance_id        | Integer      | FK → provider_instances.id     |                                |
| tier               | String(20)   | NOT NULL                       | "health", "summary", "detail" |
| data               | Text (JSON)  | NOT NULL                       | Provider-specific JSON blob    |
| fetched_at         | DateTime     | NOT NULL                       | When data was fetched          |
| is_stale           | Boolean      | NOT NULL, default False        | True when provider unreachable |

UNIQUE constraint on (instance_id, tier) — one cache entry per tier.
Upserts replace existing data on each poll.

Indexes: instance_id, (instance_id, tier) UNIQUE

### provider_action_log
Audit trail of user-triggered actions.

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| instance_id        | Integer      | FK → provider_instances.id     |                                |
| user_id            | Integer      | FK → users.id                  |                                |
| action             | String(100)  | NOT NULL                       | e.g., "sonarr.search"        |
| params             | Text (JSON)  | NULLABLE                       | Action parameters              |
| result             | String(20)   | NOT NULL                       | "success", "failure", "timeout"|
| result_message     | Text         | NULLABLE                       | Detail on result               |
| created_at         | DateTime     | NOT NULL, default utcnow       |                                |

Provides accountability: who did what, when, and what happened. Useful
for debugging and for multi-user environments where you need to trace
who triggered an action that caused an issue.

Indexes: instance_id, user_id, created_at

### metrics
Time-series data storage (v1 SQLite implementation of MetricsStore).

| Column             | Type         | Constraints                    | Notes                          |
|--------------------|--------------|--------------------------------|--------------------------------|
| id                 | Integer      | PK, autoincrement              |                                |
| metric             | String(100)  | NOT NULL                       | e.g., "pihole.queries_total"  |
| value              | Float        | NOT NULL                       |                                |
| tags               | Text (JSON)  | NOT NULL, default "{}"         | e.g., {"instance_id": 5}      |
| timestamp          | DateTime     | NOT NULL                       |                                |

Indexes: (metric, timestamp), (metric, tags, timestamp)

Retention: scheduler runs daily cleanup, deleting rows older than the
configured retention period (default: 30 days).

Note: this table is ONLY accessed through the MetricsStore abstraction.
No direct SQLAlchemy queries against this table in application code.
This allows the entire metrics backend to be swapped without touching
any other code.

## First-Time Setup Wizard

### Trigger Condition
On every request, middleware checks if the users table is empty. If it is,
all routes redirect to `/setup`. Once at least one user exists, the `/setup`
route returns 404 permanently — it cannot be re-entered.

### Wizard Flow

**Step 1: Create Admin Account**
- Username (required)
- Password + confirm password (required, local auth)
- Optional: "Link Plex Account" button to initiate Plex OAuth
  - If linked, auth_method is set to "both"
  - If skipped, auth_method is set to "local"
- Account is created with the admin role

**Step 2: Configure First Providers (Optional, Skippable)**
- Presents a checklist of available provider types
- For each selected type, collects:
  - Display name (e.g., "Sonarr (HD)")
  - URL
  - API key / credentials
- Runs a live health check on submission to verify connectivity
- If health check fails, shows error but allows saving anyway
  (user may want to configure now and fix networking later)
- User can skip this step entirely and add providers from admin UI later

**Step 3: Done**
- Summary of what was created
- Redirect to dashboard

### Security
- `/setup` route has no auth middleware (no users exist to authenticate)
- `/setup` is only functional when `SELECT COUNT(*) FROM users` returns 0
- Once the first user is committed to the database, the setup middleware
  short-circuits and never exposes the setup routes again
- All provider secrets entered during setup are encrypted before storage
  (same Fernet flow as the admin UI)

## Seed Data

### First Run Initialization
On first startup (empty database), the following is seeded:

**Roles:**
| Name   | is_system | Description                          |
|--------|-----------|--------------------------------------|
| admin  | True      | Full access to all features          |
| user   | True      | View + safe actions                  |
| viewer | True      | Read-only access                     |

**Permissions and role mappings:**
Populated dynamically from registered provider types. On startup:
1. Each provider class registers its permissions
2. Permissions are written to the permissions table
3. Default role mappings are applied:
   - admin role → all permissions
   - user role → all "read" + all "action" permissions
   - viewer role → all "read" permissions
4. If new permissions are registered (new provider added), the admin role
   automatically gets them. User and viewer roles get them according to
   their category (read/action/admin).

**First user:**
The setup wizard (on first run only) creates the initial admin user.
This user can authenticate via either method and is assigned the admin role.

## Migration Strategy

### Alembic
Schema changes are managed via Alembic migrations:
- `alembic/versions/` contains ordered migration scripts
- `alembic upgrade head` runs on app startup before any other initialization
- Migrations are idempotent — safe to run multiple times
- Downgrade paths are provided for all migrations

### Provider Schema Independence
Provider-specific data is stored as JSON blobs in config, cache data, and
metrics tags. This means adding a new provider never requires a schema
migration. Only changes to the core tables (users, roles, provider framework)
need Alembic migrations.

## Encryption Details

### What is encrypted
| Data                     | Method    | Stored In              |
|--------------------------|-----------|------------------------|
| Local passwords          | bcrypt    | users.password_hash    |
| Plex tokens              | Fernet    | users.plex_token       |
| Provider API keys        | Fernet    | provider_instances.config (secret fields) |
| Password reset tokens    | SHA-256   | password_reset_tokens.token_hash |
| Session IDs              | Random    | sessions.id (not encrypted, but unpredictable) |

### Fernet Key Management
- Derived from SECRET_KEY environment variable
- If SECRET_KEY changes, all Fernet-encrypted data becomes unrecoverable
- Admin UI should provide a key rotation utility (re-encrypt all secrets
  with a new key) — deferred to post-MVP

### What is NOT encrypted
- Provider URLs (not sensitive, needed for health check display)
- Provider display names
- Cached data (already from upstream APIs, not secrets)
- Metrics (aggregate values, not sensitive)
- Action logs (audit trail, should be readable)

## SQLite Considerations

### WAL Mode
The database is opened in WAL (Write-Ahead Logging) mode for better
concurrent read/write performance. This is important because the scheduler
writes cache data frequently while the frontend reads it.

```python
# On database initialization
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
```

### Backup
The SQLite database file is stored in the /config mount on Unraid. Standard
Unraid backup practices (appdata backup plugin) cover it. WAL mode means
the .db, .db-wal, and .db-shm files should all be backed up together.

### Size Expectations
With 13 provider instances, 60-second polling, and 30-day metric retention:
- Config/auth tables: negligible (< 1MB)
- Cache table: ~13 rows, constantly overwritten (< 1MB)
- Action log: depends on usage, ~10KB per 100 actions
- Metrics: ~500K rows per month at per-minute granularity → ~50MB/month
- Total after 6 months: ~300MB — well within SQLite capabilities
