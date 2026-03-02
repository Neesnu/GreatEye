from datetime import datetime, timedelta
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import hashlib
import secrets

import httpx

from src.auth.dependencies import get_current_user
from src.auth.local import generate_session_id, hash_password, verify_password
from src.auth.plex import check_pin, create_pin, get_auth_url, get_plex_user
from src.auth.rate_limit import RateLimiter, login_limiter
from src.config import settings
from src.database import get_db
from src.models.auth import PasswordResetToken, PlexApprovedUser
from src.models.session import Session
from src.models.user import User
from src.services.encryption import encrypt

# 3 reset requests per hour per username
reset_limiter = RateLimiter(max_attempts=3, window_seconds=3600)

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _render(request: Request, template: str, context: dict, status_code: int = 200):
    """Render full page or HTMX partial based on HX-Request header."""
    context["request"] = request
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            templates.get_template(template).render(context),
            status_code=status_code,
        )
    return templates.TemplateResponse(
        "base.html",
        {**context, "content_template": template},
        status_code=status_code,
    )


@router.get("/login")
async def login_page(request: Request) -> HTMLResponse:
    """Render the login page."""
    return _render(request, "pages/login.html", {
        "show_plex": bool(settings.plex_client_id),
    })


@router.post("/login")
async def login(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Authenticate with username and password."""
    client_ip = request.client.host if request.client else "unknown"

    # Rate limiting
    if not login_limiter.is_allowed(client_ip):
        logger.warning("login_rate_limited", ip=client_ip)
        return HTMLResponse(
            '<p class="form-error">Too many attempts. Try again later.</p>',
            status_code=429,
        )

    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")

    # Generic error for both invalid username and password
    fail_msg = '<p class="form-error">Invalid credentials</p>'

    if not username or not password:
        login_limiter.record(client_ip)
        return HTMLResponse(fail_msg, status_code=401)

    result = await db.execute(
        select(User).where(User.username == username)
    )
    user = result.scalar_one_or_none()

    if user is None or user.auth_method == "plex":
        # Perform a dummy hash to prevent timing-based username enumeration
        verify_password(password, "$2b$12$000000000000000000000uVsEnAdx6XGFXgQqIOwSAl8tONAgfb6")
        login_limiter.record(client_ip)
        logger.warning("login_failed", username=username, ip=client_ip, reason="user_not_found")
        return HTMLResponse(fail_msg, status_code=401)

    if not user.password_hash or not verify_password(password, user.password_hash):
        login_limiter.record(client_ip)
        logger.warning("login_failed", username=username, ip=client_ip, reason="invalid_password")
        return HTMLResponse(fail_msg, status_code=401)

    if not user.is_active:
        login_limiter.record(client_ip)
        logger.warning("login_failed", username=username, ip=client_ip, reason="account_disabled")
        return HTMLResponse(fail_msg, status_code=401)

    # Create session
    session_id = generate_session_id()
    expires = datetime.utcnow() + timedelta(hours=settings.session_expiry_hours)
    db.add(Session(id=session_id, user_id=user.id, expires_at=expires))

    # Update last login
    user.last_login = datetime.utcnow()

    logger.info("login_success", username=username, ip=client_ip, user_id=user.id)

    # Redirect to dashboard (HTMX will follow HX-Redirect)
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


@router.post("/logout")
async def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Destroy the current session and redirect to login."""
    session_id = request.cookies.get("session_id")
    if session_id:
        await db.execute(delete(Session).where(Session.id == session_id))

    logger.info("logout", user_id=user.id, username=user.username)

    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie("session_id", path="/")
    return response


@router.get("/change-password")
async def change_password_page(
    request: Request,
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Render the change password form."""
    return _render(request, "pages/change_password.html", {
        "user": user,
        "forced": user.force_reset,
    })


@router.post("/change-password")
async def change_password(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Process password change."""
    form = await request.form()
    current = form.get("current_password", "")
    new_pw = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    # Validate current password (skip if force_reset — admin may have set it)
    if not user.force_reset:
        if not user.password_hash or not verify_password(current, user.password_hash):
            return HTMLResponse(
                '<p class="form-error">Current password is incorrect</p>',
                status_code=400,
            )

    if len(new_pw) < 8:
        return HTMLResponse(
            '<p class="form-error">Password must be at least 8 characters</p>',
            status_code=400,
        )

    if new_pw != confirm:
        return HTMLResponse(
            '<p class="form-error">Passwords do not match</p>',
            status_code=400,
        )

    # Update password
    user.password_hash = hash_password(new_pw)
    user.force_reset = False
    db.add(user)

    # Invalidate all other sessions for this user
    current_session_id = request.cookies.get("session_id")
    await db.execute(
        delete(Session).where(
            Session.user_id == user.id,
            Session.id != current_session_id,
        )
    )

    logger.info("password_changed", user_id=user.id, username=user.username)

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/"},
    )


# ---------------------------------------------------------------------------
# Plex OAuth
# ---------------------------------------------------------------------------

@router.get("/plex")
async def plex_login(request: Request) -> RedirectResponse:
    """Initiate Plex OAuth by creating a PIN and redirecting to Plex."""
    if not settings.plex_client_id:
        return RedirectResponse(url="/auth/login", status_code=302)

    try:
        pin = await create_pin()
    except httpx.HTTPError as e:
        logger.error("plex_pin_create_failed", error=str(e))
        return RedirectResponse(url="/auth/login?error=plex_unavailable", status_code=302)

    callback_url = str(request.url_for("plex_callback"))
    auth_url = get_auth_url(pin, callback_url)

    # Store pin_id in a short-lived cookie so callback can retrieve it
    response = RedirectResponse(url=auth_url, status_code=302)
    response.set_cookie(
        key="plex_pin_id",
        value=str(pin.pin_id),
        httponly=True,
        samesite="lax",
        max_age=600,
        path="/auth/plex",
    )
    return response


@router.get("/plex/callback", response_model=None)
async def plex_callback(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    """Complete Plex OAuth: verify PIN, look up approved user, create session."""
    pin_id_str = request.cookies.get("plex_pin_id")
    if not pin_id_str:
        return RedirectResponse(url="/auth/login?error=plex_no_pin", status_code=302)

    try:
        pin_id = int(pin_id_str)
        auth_token = await check_pin(pin_id)
    except (ValueError, httpx.HTTPError) as e:
        logger.error("plex_pin_check_failed", error=str(e))
        return RedirectResponse(url="/auth/login?error=plex_failed", status_code=302)

    if not auth_token:
        return RedirectResponse(url="/auth/login?error=plex_not_claimed", status_code=302)

    try:
        plex_user = await get_plex_user(auth_token)
    except httpx.HTTPError as e:
        logger.error("plex_user_fetch_failed", error=str(e))
        return RedirectResponse(url="/auth/login?error=plex_failed", status_code=302)

    # Check if this Plex user is approved
    result = await db.execute(
        select(PlexApprovedUser).where(
            PlexApprovedUser.plex_username == plex_user.username
        )
    )
    approved = result.scalar_one_or_none()

    if approved is None:
        logger.warning(
            "plex_login_denied",
            plex_username=plex_user.username,
            reason="not_approved",
        )
        return RedirectResponse(url="/auth/login?error=plex_not_approved", status_code=302)

    # Look up existing user by plex_user_id
    result = await db.execute(
        select(User).where(User.plex_user_id == plex_user.plex_user_id)
    )
    user = result.scalar_one_or_none()

    encrypted_token = encrypt(plex_user.auth_token)

    if user is None:
        # Also check if a local user with the same username exists (for linking)
        result = await db.execute(
            select(User).where(User.username == plex_user.username)
        )
        existing_local = result.scalar_one_or_none()

        if existing_local and existing_local.plex_user_id is None:
            # Link Plex to existing local account
            existing_local.plex_user_id = plex_user.plex_user_id
            existing_local.plex_token = encrypted_token
            existing_local.auth_method = "both"
            user = existing_local
            logger.info(
                "plex_account_linked",
                user_id=user.id,
                plex_username=plex_user.username,
            )
        else:
            # Create new Plex-only user
            user = User(
                username=plex_user.username,
                auth_method="plex",
                plex_user_id=plex_user.plex_user_id,
                plex_token=encrypted_token,
                email=plex_user.email,
                role_id=approved.default_role_id,
            )
            db.add(user)
            await db.flush()
            logger.info(
                "plex_user_created",
                user_id=user.id,
                plex_username=plex_user.username,
            )
    else:
        # Refresh token on existing user
        user.plex_token = encrypted_token
        logger.info(
            "plex_token_refreshed",
            user_id=user.id,
            plex_username=plex_user.username,
        )

    if not user.is_active:
        return RedirectResponse(url="/auth/login?error=account_disabled", status_code=302)

    # Create session
    session_id = generate_session_id()
    expires = datetime.utcnow() + timedelta(hours=settings.session_expiry_hours)
    db.add(Session(id=session_id, user_id=user.id, expires_at=expires))
    user.last_login = datetime.utcnow()

    logger.info("plex_login_success", user_id=user.id, plex_username=plex_user.username)

    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=settings.session_expiry_hours * 3600,
        path="/",
        secure=request.url.scheme == "https",
    )
    response.delete_cookie("plex_pin_id", path="/auth/plex")
    return response


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------

@router.get("/reset-request")
async def reset_request_page(request: Request) -> HTMLResponse:
    """Render the password reset request form."""
    return templates.TemplateResponse("pages/reset_request.html", {"request": request})


@router.post("/reset-request")
async def reset_request(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Generate a password reset token for the given username."""
    form = await request.form()
    username = form.get("username", "").strip()

    # Always return same message to avoid user enumeration
    success_msg = (
        '<p class="form-success">If an account with that username exists, '
        "a reset has been created. Contact your admin for the reset link.</p>"
    )

    if not username:
        return HTMLResponse(success_msg, status_code=200)

    # Rate limiting
    if not reset_limiter.is_allowed(username):
        logger.warning("reset_rate_limited", username=username)
        return HTMLResponse(success_msg, status_code=200)

    reset_limiter.record(username)

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if user is None or user.auth_method == "plex":
        # Don't reveal whether user exists
        return HTMLResponse(success_msg, status_code=200)

    # Generate token
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    reset = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db.add(reset)

    logger.info("reset_token_created", user_id=user.id, username=username)

    # In v1, the token is surfaced to the admin in the admin UI.
    # Store the plaintext token temporarily so admin can see it.
    # We return the success message to the user — admin gets the link.
    # For now, log it (admin can see it in logs or admin UI later).
    logger.info(
        "reset_token_for_admin",
        username=username,
        reset_url=f"/auth/reset/{token}",
    )

    return HTMLResponse(success_msg, status_code=200)


@router.get("/reset/{token}")
async def reset_page(
    request: Request, token: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Render password reset form if token is valid."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.utcnow(),
        )
    )
    reset = result.scalar_one_or_none()

    if reset is None:
        return templates.TemplateResponse(
            "pages/reset_invalid.html", {"request": request}, status_code=400
        )

    return templates.TemplateResponse(
        "pages/reset_form.html", {"request": request, "token": token}
    )


@router.post("/reset/{token}")
async def reset_password(
    request: Request, token: str, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Process password reset with a valid token."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.utcnow(),
        )
    )
    reset = result.scalar_one_or_none()

    if reset is None:
        return HTMLResponse(
            '<p class="form-error">Invalid or expired reset token</p>',
            status_code=400,
        )

    form = await request.form()
    new_pw = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    if len(new_pw) < 8:
        return HTMLResponse(
            '<p class="form-error">Password must be at least 8 characters</p>',
            status_code=400,
        )

    if new_pw != confirm:
        return HTMLResponse(
            '<p class="form-error">Passwords do not match</p>',
            status_code=400,
        )

    # Update password
    result = await db.execute(select(User).where(User.id == reset.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return HTMLResponse(
            '<p class="form-error">User not found</p>', status_code=400
        )

    user.password_hash = hash_password(new_pw)
    user.force_reset = False

    # Mark token as used
    reset.used = True

    # Invalidate all sessions
    await db.execute(delete(Session).where(Session.user_id == user.id))

    logger.info("password_reset_complete", user_id=user.id, username=user.username)

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/auth/login"},
    )
