from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from alembic import command
from alembic.config import Config

from fastapi.responses import JSONResponse

from src.auth.middleware import AuthMiddleware
from src.config import settings
from src.database import engine, init_db
from src.routes.admin import router as admin_router
from src.routes.auth import router as auth_router
from src.routes.dashboard import router as dashboard_router
from src.routes.preferences import router as preferences_router
from src.routes.providers import router as providers_router
from src.routes.setup import router as setup_router
from src.providers.registry import registry
from src.providers.scheduler import scheduler
from src.services.health import get_health_status, reset_start_time
from src.services.seed import run_seed
from src.utils.logging import configure_logging

configure_logging(log_level=settings.log_level)
logger = structlog.get_logger()

BASE_DIR = Path(__file__).resolve().parent.parent


async def run_migrations() -> None:
    """Run Alembic migrations to head using the existing async engine."""
    alembic_cfg = Config(str(BASE_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))

    async with engine.begin() as conn:
        await conn.run_sync(_do_upgrade, alembic_cfg)


def _do_upgrade(connection, alembic_cfg: Config) -> None:
    """Synchronous callback that runs Alembic with the given connection."""
    alembic_cfg.attributes["connection"] = connection
    command.upgrade(alembic_cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("greateye_starting")
    reset_start_time()
    await init_db()
    logger.info("database_initialized")
    await run_migrations()
    logger.info("migrations_complete")
    await run_seed()
    logger.info("seed_complete")
    await registry.discover_and_register()
    logger.info("providers_registered")
    await registry.initialize_instances()
    logger.info("providers_initialized")
    await scheduler.start_retention_cleanup(settings.metrics_retention_days)
    logger.info("retention_cleanup_scheduled", days=settings.metrics_retention_days)
    yield
    await registry.shutdown()
    await engine.dispose()
    logger.info("greateye_stopped")


app = FastAPI(title="Great Eye", lifespan=lifespan)

# Middleware (outermost first)
app.add_middleware(AuthMiddleware)

# Static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Templates
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Routes
app.include_router(auth_router)
app.include_router(setup_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(preferences_router)
app.include_router(providers_router)


@app.get("/")
async def index() -> RedirectResponse:
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/health")
async def health() -> JSONResponse:
    """Self-health endpoint per H6. No auth required."""
    status = await get_health_status()
    http_status = 200 if status["status"] == "ok" else 503
    return JSONResponse(content=status, status_code=http_status)
