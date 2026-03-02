import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.database import get_db
from src.models.session import Session
from src.models.user import User

logger = structlog.get_logger()

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.post("/delivery-mode")
async def set_delivery_mode(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Toggle between SSE and batch delivery mode."""
    form = await request.form()
    mode = form.get("mode", "sse")
    if mode not in ("sse", "batch"):
        mode = "sse"

    session_id = request.cookies.get("session_id")
    if session_id:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.delivery_mode = mode
            await db.commit()

    logger.info("delivery_mode_changed", user_id=user.id, mode=mode)

    return HTMLResponse("", headers={"HX-Refresh": "true"})
