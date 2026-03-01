from datetime import datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models.session import Session
from src.models.user import User


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """FastAPI dependency: load and validate the current user from session cookie."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()

    if session is None or session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session expired")

    result = await db.execute(
        select(User).where(User.id == session.user_id)
    )
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")

    # Attach permissions as a set for fast lookup
    user.permission_keys = {p.key for p in user.role.permissions}
    return user


def require_permission(permission_key: str):
    """FastAPI dependency factory for permission checks."""
    async def checker(user: User = Depends(get_current_user)) -> User:
        if permission_key not in user.permission_keys:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker
