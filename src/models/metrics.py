from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(String(100))
    value: Mapped[float] = mapped_column(Float)
    tags: Mapped[str] = mapped_column(Text, server_default="{}")
    timestamp: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_metrics_metric_timestamp", "metric", "timestamp"),
        Index("ix_metrics_metric_tags_timestamp", "metric", "tags", "timestamp"),
    )
