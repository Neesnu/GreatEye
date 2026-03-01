from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    auth_method: Mapped[str] = mapped_column(String(20), default="local")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    plex_token: Mapped[str | None] = mapped_column(Text)
    plex_user_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, index=True
    )
    email: Mapped[str | None] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id"), index=True
    )
    force_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    role: Mapped["Role"] = relationship(back_populates="users", lazy="selectin")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user")
