from datetime import datetime

from sqlalchemy import func, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from src.database import async_session_factory
from src.models.session import Session
from src.models.user import User

# Routes that never require authentication — prefix-matched (must end with /)
EXEMPT_PREFIXES = (
    "/static/",
    "/auth/login/",
    "/auth/plex/",
    "/auth/reset/",
    "/setup/",
)

# Exact-match exempt routes
EXEMPT_PATHS = {
    "/auth/login",
    "/auth/plex",
    "/auth/reset",
    "/setup",
    "/health",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces the auth flow:

    1. Setup check — if no users exist, redirect to /setup
    2. Session validation — read cookie, validate, load user
    3. Force reset check — redirect to /auth/change-password if flagged
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Exempt routes bypass all auth checks
        if path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        async with async_session_factory() as db:
            # 1. Setup check — if no users exist, redirect to /setup
            result = await db.execute(select(func.count(User.id)))
            user_count = result.scalar()
            if user_count == 0:
                return RedirectResponse(url="/setup", status_code=302)

            # 2. Session validation
            session_id = request.cookies.get("session_id")
            if not session_id:
                return RedirectResponse(url="/auth/login", status_code=302)

            result = await db.execute(
                select(Session).where(Session.id == session_id)
            )
            session = result.scalar_one_or_none()

            if session is None or session.expires_at < datetime.utcnow():
                response = RedirectResponse(url="/auth/login", status_code=302)
                response.delete_cookie("session_id")
                return response

            result = await db.execute(
                select(User).where(User.id == session.user_id)
            )
            user = result.scalar_one_or_none()

            if user is None or not user.is_active:
                response = RedirectResponse(url="/auth/login", status_code=302)
                response.delete_cookie("session_id")
                return response

            # 3. Force reset check
            if user.force_reset and path != "/auth/change-password":
                return RedirectResponse(
                    url="/auth/change-password", status_code=302
                )

        return await call_next(request)
