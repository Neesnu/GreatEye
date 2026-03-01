from datetime import datetime, timedelta
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.local import generate_session_id, hash_password
from src.config import settings
from src.database import get_db
from src.models.role import Role
from src.models.session import Session
from src.models.user import User

logger = structlog.get_logger()

router = APIRouter(prefix="/setup", tags=["setup"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


async def _has_users(db: AsyncSession) -> bool:
    """Check if any users exist in the database."""
    result = await db.execute(select(func.count(User.id)))
    return result.scalar() > 0


@router.get("")
async def setup_page(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Render the setup wizard. Only accessible when no users exist."""
    if await _has_users(db):
        return HTMLResponse(status_code=404, content="Not found")

    return templates.TemplateResponse("pages/setup.html", {"request": request})


@router.post("/admin")
async def create_admin(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Create the first admin account."""
    if await _has_users(db):
        return HTMLResponse(status_code=404, content="Not found")

    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    confirm = form.get("confirm_password", "")

    # Validation
    if not username:
        return HTMLResponse(
            '<p class="form-error">Username is required</p>', status_code=400
        )

    if len(username) < 3:
        return HTMLResponse(
            '<p class="form-error">Username must be at least 3 characters</p>',
            status_code=400,
        )

    if len(password) < 8:
        return HTMLResponse(
            '<p class="form-error">Password must be at least 8 characters</p>',
            status_code=400,
        )

    if password != confirm:
        return HTMLResponse(
            '<p class="form-error">Passwords do not match</p>', status_code=400
        )

    # Get admin role
    result = await db.execute(select(Role).where(Role.name == "admin"))
    admin_role = result.scalar_one_or_none()
    if admin_role is None:
        return HTMLResponse(
            '<p class="form-error">System error: admin role not found</p>',
            status_code=500,
        )

    # Create user
    user = User(
        username=username,
        auth_method="local",
        password_hash=hash_password(password),
        role_id=admin_role.id,
    )
    db.add(user)
    await db.flush()

    # Create session immediately — log the admin in
    session_id = generate_session_id()
    expires = datetime.utcnow() + timedelta(hours=settings.session_expiry_hours)
    db.add(Session(id=session_id, user_id=user.id, expires_at=expires))

    logger.info("setup_admin_created", username=username, user_id=user.id)

    response = HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/"},
    )
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=settings.session_expiry_hours * 3600,
        path="/",
        secure=request.url.scheme == "https",
    )
    return response
