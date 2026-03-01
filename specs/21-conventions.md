# Conventions & Patterns

## Overview
This document defines the coding conventions, dependency versions,
type definitions, and architectural patterns that all code must follow.
An implementing agent should read this document FIRST before writing
any code. Consistency across modules is more important than any
individual preference.

## Python Version & Style

- Python 3.12+
- Type hints on all function signatures (parameters and return types)
- async/await for all I/O operations (database, HTTP, filesystem)
- No bare `except:` — always catch specific exceptions
- f-strings for string formatting (not .format() or %)
- Double quotes for strings (not single quotes)
- Maximum line length: 100 characters (not 79)
- Imports sorted: stdlib, third-party, local (isort compatible)

## Dependencies (pinned)

```
# requirements.txt

# Web framework
fastapi==0.115.*
uvicorn[standard]==0.34.*
starlette==0.41.*

# Templates
jinja2==3.1.*

# Database
sqlalchemy[asyncio]==2.0.*
aiosqlite==0.20.*
alembic==1.14.*

# HTTP client
httpx==0.28.*

# Auth & crypto
bcrypt==4.2.*
cryptography==44.*

# Structured logging
structlog==24.*

# Utilities
python-multipart==0.0.*    # Form parsing for FastAPI
```

### Key Version Decisions
- **SQLAlchemy 2.0**: Async-native with `AsyncSession`. All database
  access uses the 2.0 query style (`select()`, `session.execute()`),
  NOT the legacy 1.x `session.query()` pattern.
- **httpx**: Async HTTP client. Used for all upstream provider API calls.
  NOT requests, NOT aiohttp.
- **structlog**: Structured logging with JSON output in production,
  pretty console output in development.

## Project Structure

```
greateye/
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── static/
│   ├── css/
│   │   └── greateye.css
│   ├── js/
│   │   ├── htmx.min.js
│   │   └── htmx-sse.js
│   └── icons/
├── templates/
│   ├── base.html
│   ├── partials/
│   ├── pages/
│   ├── cards/
│   ├── detail/
│   └── admin/
├── src/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, lifespan, middleware
│   ├── config.py                # Settings from environment
│   ├── database.py              # Engine, session factory, base model
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── session.py
│   │   ├── role.py
│   │   ├── provider.py
│   │   └── metrics.py
│   ├── schemas/                 # Pydantic models (NOT ORM — for validation)
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   └── provider.py
│   ├── auth/                    # Auth logic
│   │   ├── __init__.py
│   │   ├── middleware.py
│   │   ├── local.py
│   │   ├── plex.py
│   │   └── dependencies.py      # FastAPI Depends() for auth
│   ├── providers/               # Provider implementations
│   │   ├── __init__.py
│   │   ├── base.py              # BaseProvider, result types
│   │   ├── arr_base.py          # ArrBaseProvider
│   │   ├── registry.py          # Provider registry
│   │   ├── scheduler.py         # Polling scheduler
│   │   ├── cache.py             # Cache read/write layer
│   │   ├── event_bus.py         # In-memory pub/sub for SSE
│   │   ├── qbittorrent.py
│   │   ├── sonarr.py
│   │   ├── radarr.py
│   │   ├── prowlarr.py
│   │   ├── seerr.py
│   │   ├── plex.py
│   │   ├── tautulli.py
│   │   ├── pihole.py
│   │   ├── unbound.py
│   │   └── docker.py
│   ├── routes/                  # FastAPI route modules
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── providers.py
│   │   ├── admin.py
│   │   └── setup.py
│   ├── services/                # Business logic layer
│   │   ├── __init__.py
│   │   ├── encryption.py        # Fernet key derivation, encrypt/decrypt
│   │   ├── metrics.py           # MetricsStore implementation
│   │   └── health.py            # Self-health endpoint logic
│   └── utils/
│       ├── __init__.py
│       ├── formatting.py        # format_bytes, format_speed, format_eta
│       └── validation.py        # URL validation, param validation
└── tests/
    ├── conftest.py              # Shared fixtures
    ├── fixtures/                # Mock API response data
    │   ├── sonarr/
    │   ├── radarr/
    │   ├── qbittorrent/
    │   └── ...
    ├── test_providers/
    │   ├── test_base.py
    │   ├── test_sonarr.py
    │   └── ...
    ├── test_routes/
    ├── test_auth/
    └── test_services/
```

## Core Type Definitions

All provider result types are defined in `src/providers/base.py` as
dataclasses. NOT Pydantic models (providers don't need validation,
they produce data). Pydantic is reserved for request validation in
routes (the `schemas/` directory).

```python
# src/providers/base.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


@dataclass
class HealthResult:
    status: HealthStatus
    message: str
    response_time_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SummaryResult:
    data: dict[str, Any]
    """Provider-specific summary data. Shape defined per provider spec."""

    partial: bool = False
    """True if some data sources failed but partial data is available."""

    errors: list[str] = field(default_factory=list)
    """Error messages for any data sources that failed."""


@dataclass
class DetailResult:
    data: dict[str, Any]
    """Provider-specific detail data. Shape defined per provider spec."""

    partial: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class ActionDefinition:
    key: str
    display_name: str
    permission: str
    confirm: bool = False
    confirm_message: str = ""
    params_schema: dict[str, Any] = field(default_factory=dict)
    """JSON Schema for action parameters. Used for validation."""


@dataclass
class ActionResult:
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    invalidate_cache: bool = False
    """If True, registry invalidates summary cache after this action."""


@dataclass
class ProviderMeta:
    type_id: str
    """Unique identifier, e.g., 'sonarr'. Used as DB key."""

    display_name: str
    """Human-readable name, e.g., 'Sonarr'."""

    icon: str
    """Icon filename from static/icons/, e.g., 'tv.svg'."""

    category: str
    """Grouping category, e.g., 'Media Management'."""

    config_schema: dict[str, Any]
    """JSON config schema with field definitions."""

    default_intervals: dict[str, int]
    """Default polling intervals in seconds."""

    permissions: list["PermissionDef"] = field(default_factory=list)


@dataclass
class PermissionDef:
    key: str
    """e.g., 'sonarr.search'"""

    display_name: str
    category: str
    """'read', 'action', or 'admin'"""

    description: str = ""
```

## Configuration Pattern

Settings loaded from environment variables via a single config object:

```python
# src/config.py

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Required
    secret_key: str

    # Optional with defaults
    database_url: str = "sqlite+aiosqlite:///config/greateye.db"
    plex_client_id: str = ""
    log_level: str = "INFO"
    session_expiry_hours: int = 24
    metrics_retention_days: int = 30

    model_config = {"env_prefix": "", "case_sensitive": False}


# Singleton — imported wherever needed
settings = Settings()
```

Usage anywhere:
```python
from src.config import settings

key = settings.secret_key
```

## Database Session Pattern

```python
# src/database.py

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},  # SQLite
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency for database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Called on startup. Configures SQLite pragmas."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.execute(text("PRAGMA busy_timeout=5000"))
```

**Critical rule:** Never hold a database session across an `await`
that could block indefinitely (SSE streams, long HTTP calls, sleeps).
Open session → read/write → close session → then do async work.

## ORM Model Pattern

```python
# src/models/user.py

from sqlalchemy import String, Boolean, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    auth_method: Mapped[str] = mapped_column(String(10), default="local")
    password_hash: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))
    plex_user_id: Mapped[str | None] = mapped_column(String(50))
    plex_token: Mapped[str | None] = mapped_column(String(500))
    role_id: Mapped[int] = mapped_column(Integer)
    force_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_login: Mapped[DateTime | None] = mapped_column(DateTime)
```

All models use SQLAlchemy 2.0 `Mapped` type annotations. No legacy
`Column()` syntax.

## HTTP Client Pattern (Providers)

The registry creates one `httpx.AsyncClient` per provider instance
at initialization. The client is reused for all requests during the
instance's lifetime and closed on cleanup.

```python
# In registry, when initializing a provider instance:

client = httpx.AsyncClient(
    base_url=config["url"],
    timeout=httpx.Timeout(
        connect=5.0,
        read=10.0,
        write=5.0,
        pool=5.0,
    ),
    headers={
        "User-Agent": "GreatEye/1.0",
        "Accept": "application/json",
    },
    follow_redirects=True,
)
provider = SonarrProvider(
    instance_id=instance_id,
    display_name=display_name,
    config=decrypted_config,
)
provider.http_client = client
```

Providers never create their own httpx clients. The registry owns
the client lifecycle.

## Logging Pattern

```python
# At module level
import structlog

logger = structlog.get_logger()

# In provider methods — always bind instance context
async def get_summary(self) -> SummaryResult:
    log = logger.bind(
        provider_type=self.meta().type_id,
        instance_id=self.instance_id,
    )
    log.info("summary_fetch_started")
    try:
        # ... fetch data ...
        log.info("summary_fetch_complete", duration_ms=elapsed)
        return SummaryResult(data=data)
    except Exception as e:
        log.error("summary_fetch_failed", error=str(e))
        return SummaryResult(data={}, errors=[str(e)])
```

## Template Rendering Pattern

```python
# src/routes/dashboard.py

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from src.auth.dependencies import get_current_user
from src.providers.registry import registry

router = APIRouter()


@router.get("/dashboard")
async def dashboard(request: Request, user=Depends(get_current_user)):
    instances = await registry.get_dashboard_state(user)
    context = {
        "request": request,
        "user": user,
        "instances": instances,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("pages/dashboard.html", context)
    return templates.TemplateResponse("base.html", {
        **context,
        "content_template": "pages/dashboard.html",
    })
```

The `HX-Request` header detection pattern is used on every route.
Full page loads get the shell + content. HTMX requests get just
the content partial.

## Permission Check Pattern

```python
# src/auth/dependencies.py

from fastapi import Depends, HTTPException, Request

async def get_current_user(request: Request, db=Depends(get_db)):
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(401)
    # ... validate session, load user + role + permissions ...
    return user


def require_permission(permission_key: str):
    """FastAPI dependency factory for permission checks."""
    async def checker(user=Depends(get_current_user)):
        if permission_key not in user.permissions:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return checker


# Usage in routes:
@router.post("/providers/{id}/actions/{action}")
async def execute_action(
    id: str,
    action: str,
    user=Depends(get_current_user),
):
    # Permission check happens inside registry.execute_action()
    # which looks up the required permission for the action
    result = await registry.execute_action(id, action, params, user)
    ...
```

## Test Patterns

### Fixture Files
Each provider has a `tests/fixtures/{provider}/` directory containing
JSON files with sample API responses:

```
tests/fixtures/sonarr/
  system_status.json        # GET /api/v3/system/status response
  health.json               # GET /api/v3/health response
  series.json               # GET /api/v3/series response
  queue.json                # GET /api/v3/queue response
```

These are real (sanitized) responses captured from actual services.
They serve as the contract test — if the fixture matches the spec's
data shape, the provider implementation is correct.

### Mock HTTP Client
Providers are tested with a mock httpx client that returns fixtures:

```python
# tests/conftest.py

import json
import httpx
import pytest
from pathlib import Path


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: dict[str, dict]):
        self.responses = responses  # {path: {"status": 200, "json": {...}}}

    async def handle_async_request(self, request):
        path = request.url.path
        if path in self.responses:
            resp = self.responses[path]
            return httpx.Response(
                status_code=resp.get("status", 200),
                json=resp.get("json", {}),
            )
        return httpx.Response(status_code=404)


def load_fixture(provider: str, name: str) -> dict:
    path = Path(f"tests/fixtures/{provider}/{name}.json")
    return json.loads(path.read_text())


@pytest.fixture
def sonarr_provider():
    """Create a SonarrProvider with mocked HTTP responses."""
    responses = {
        "/api/v3/system/status": {
            "json": load_fixture("sonarr", "system_status")
        },
        "/api/v3/health": {
            "json": load_fixture("sonarr", "health")
        },
        "/api/v3/series": {
            "json": load_fixture("sonarr", "series")
        },
        "/api/v3/queue": {
            "json": load_fixture("sonarr", "queue")
        },
    }
    transport = MockTransport(responses)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    provider = SonarrProvider(
        instance_id="test-sonarr",
        display_name="Test Sonarr",
        config={"url": "http://test", "api_key": "test-key"},
    )
    provider.http_client = client
    return provider
```

### Test Structure
```python
# tests/test_providers/test_sonarr.py

import pytest

@pytest.mark.asyncio
async def test_health_check_up(sonarr_provider):
    result = await sonarr_provider.health_check()
    assert result.status == HealthStatus.UP
    assert "Connected" in result.message

@pytest.mark.asyncio
async def test_summary_data_shape(sonarr_provider):
    result = await sonarr_provider.get_summary()
    assert not result.partial
    assert "series_count" in result.data
    assert "queue" in result.data
    assert isinstance(result.data["series_count"], int)

@pytest.mark.asyncio
async def test_health_check_bad_api_key():
    """Test that 401 maps to DOWN with correct message."""
    responses = {
        "/api/v3/system/status": {"status": 401, "json": {}},
    }
    # ... setup provider with 401 response ...
    result = await provider.health_check()
    assert result.status == HealthStatus.DOWN
    assert "API key" in result.message
```

### What to Test Per Provider
1. **health_check**: UP, DEGRADED, DOWN for each status mapping in spec
2. **get_summary**: correct data shape, partial data handling
3. **get_detail**: correct data shape
4. **execute_action**: success and failure for each action
5. **validate_config**: success, bad credentials, wrong app, unreachable

### Database Tests
Use an in-memory SQLite database for route/service tests:

```python
@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        yield session
```

## Naming Conventions

| Thing               | Convention                    | Example                    |
|---------------------|-------------------------------|----------------------------|
| Provider class      | `{Name}Provider`              | `SonarrProvider`           |
| Provider file       | `{name}.py`                   | `sonarr.py`                |
| Provider type_id    | lowercase, no underscores     | `"sonarr"`, `"qbittorrent"`|
| ORM model class     | PascalCase singular           | `User`, `ProviderInstance` |
| DB table name       | snake_case plural             | `users`, `provider_instances` |
| Route function      | snake_case verb               | `get_dashboard`, `execute_action` |
| Template file       | kebab or snake, `.html`       | `sonarr.html`, `base.html` |
| CSS class           | BEM-ish: `block--modifier`    | `card--degraded`, `toast--error` |
| Config field key    | snake_case                    | `api_key`, `socket_path`   |
| Permission key      | `{provider}.{action}`         | `sonarr.search`            |
| Metric name         | `{provider}.{metric}`         | `sonarr.queue_count`       |
