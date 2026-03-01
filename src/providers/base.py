from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class HealthStatus(Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


@dataclass
class HealthResult:
    status: HealthStatus
    message: str
    response_time_ms: float | None = None
    details: dict[str, Any] | None = None


@dataclass
class SummaryResult:
    data: dict[str, Any]
    fetched_at: datetime


@dataclass
class DetailResult:
    data: dict[str, Any]
    fetched_at: datetime


@dataclass
class ActionDefinition:
    key: str
    display_name: str
    permission: str
    category: str
    confirm: bool = False
    confirm_message: str = ""
    params_schema: dict[str, Any] | None = None


@dataclass
class ActionResult:
    success: bool
    message: str
    data: dict[str, Any] | None = None
    invalidate_cache: bool = True


@dataclass
class PermissionDef:
    key: str
    display_name: str
    description: str
    category: str


@dataclass
class ProviderMeta:
    type_id: str
    display_name: str
    icon: str
    category: str
    config_schema: dict[str, Any]
    default_intervals: dict[str, int]
    permissions: list[PermissionDef]


class BaseProvider(ABC):
    """Abstract base class for all Great Eye providers."""

    def __init__(self, instance_id: int, display_name: str, config: dict[str, Any]) -> None:
        self.instance_id = instance_id
        self.display_name = display_name
        self.config = config
        self.http_client: Any = None  # Set by registry after instantiation

    @staticmethod
    @abstractmethod
    def meta() -> ProviderMeta:
        """Return provider type metadata. Called during auto-discovery."""
        ...

    @abstractmethod
    async def health_check(self) -> HealthResult:
        """Check upstream service health. Must complete within 5s. Must not raise."""
        ...

    @abstractmethod
    async def get_summary(self) -> SummaryResult:
        """Fetch key metrics for dashboard card. Must complete within 10s. Must not raise."""
        ...

    @abstractmethod
    async def get_detail(self) -> DetailResult:
        """Fetch detailed data for drill-down view. Must complete within 15s. Must not raise."""
        ...

    @abstractmethod
    def get_actions(self) -> list[ActionDefinition]:
        """Return list of actions this provider supports. Not async — static definitions."""
        ...

    @abstractmethod
    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        """Execute a user-triggered action. Must complete within 30s. Must not raise."""
        ...

    async def cleanup(self) -> None:
        """Optional. Called when instance is disabled or removed."""
        pass

    async def validate_config(self) -> tuple[bool, str]:
        """Optional. Validate config beyond schema checks. Returns (is_valid, message)."""
        return True, "OK"
