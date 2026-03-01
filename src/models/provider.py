from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ProviderType(Base):
    __tablename__ = "provider_types"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    config_schema: Mapped[str] = mapped_column(Text)
    default_intervals: Mapped[str] = mapped_column(Text)


class ProviderInstance(Base):
    __tablename__ = "provider_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_type_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("provider_types.id"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(100))
    config: Mapped[str] = mapped_column(Text)
    health_interval: Mapped[int] = mapped_column(Integer)
    summary_interval: Mapped[int] = mapped_column(Integer)
    detail_cache_ttl: Mapped[int] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ProviderInstanceState(Base):
    __tablename__ = "provider_instance_state"

    instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_instances.id"), primary_key=True
    )
    health_status: Mapped[str] = mapped_column(String(20), default="unknown")
    health_message: Mapped[str | None] = mapped_column(String(255))
    last_health_check: Mapped[datetime | None] = mapped_column(DateTime)
    last_successful: Mapped[datetime | None] = mapped_column(DateTime)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ProviderCache(Base):
    __tablename__ = "provider_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_instances.id"), index=True
    )
    tier: Mapped[str] = mapped_column(String(20))
    data: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("instance_id", "tier", name="uq_cache_instance_tier"),
    )


class ProviderActionLog(Base):
    __tablename__ = "provider_action_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_instances.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(100))
    params: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(20))
    result_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
