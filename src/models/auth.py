from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class PlexApprovedUser(Base):
    __tablename__ = "plex_approved_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    plex_username: Mapped[str] = mapped_column(String(100), unique=True)
    default_role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id")
    )
    approved_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
