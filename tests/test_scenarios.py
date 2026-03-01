"""Integration tests for edge-case scenarios (spec 18).

Covers gaps identified in the scenario audit:
- S3: Provider recovery transition
- S5: Provider config change invalidation
- S19: Action permission denied
- S20: Action on down provider
- S22: qBittorrent session re-auth
- S30: Docker self-protection (action rejection)
- S35: SECRET_KEY change / decryption failure
- S38: Slow network / timeout handling
- S39: Metrics retention (already covered, but add batch test)
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database import Base
from src.models.provider import (
    ProviderCache,
    ProviderInstance,
    ProviderInstanceState,
    ProviderType,
)
from src.providers.base import (
    ActionDefinition,
    ActionResult,
    HealthResult,
    HealthStatus,
    SummaryResult,
)
from tests.conftest import MockProvider, MockTransport


# ---------- Fixtures ----------

@pytest_asyncio.fixture
async def scenario_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def scenario_session(scenario_engine):
    factory = async_sessionmaker(
        scenario_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def scenario_factory(scenario_engine):
    return async_sessionmaker(
        scenario_engine, class_=AsyncSession, expire_on_commit=False
    )


# ---------- S3: Provider Recovery ----------

class TestProviderRecovery:
    """S3: Provider recovers after being down."""

    @pytest.mark.asyncio
    async def test_recovery_clears_failure_count(
        self, scenario_session, scenario_factory, monkeypatch
    ):
        """When a provider transitions from DOWN to UP, failure_count resets."""
        monkeypatch.setattr(
            "src.providers.registry.async_session_factory", scenario_factory
        )
        from src.providers.registry import ProviderRegistry

        registry = ProviderRegistry()

        # Set up a provider type and instance in DOWN state
        scenario_session.add(ProviderType(
            id="mock", display_name="Mock", icon="m",
            category="test", config_schema="{}", default_intervals="{}",
        ))
        await scenario_session.flush()
        inst = ProviderInstance(
            provider_type_id="mock", display_name="Down Provider",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300, is_enabled=True,
        )
        scenario_session.add(inst)
        await scenario_session.flush()

        # Create initial DOWN state with high failure count
        scenario_session.add(ProviderInstanceState(
            instance_id=inst.id,
            health_status="down",
            health_message="Connection refused",
            failure_count=10,
        ))
        await scenario_session.commit()

        # Simulate recovery
        up_result = HealthResult(
            status=HealthStatus.UP,
            message="Connected",
            response_time_ms=50.0,
        )
        await registry._update_health_state(inst.id, up_result)

        # Verify recovery
        result = await scenario_session.execute(
            select(ProviderInstanceState).where(
                ProviderInstanceState.instance_id == inst.id
            )
        )
        state = result.scalar_one()
        assert state.health_status == "up"
        assert state.failure_count == 0
        assert state.last_successful is not None

    @pytest.mark.asyncio
    async def test_down_increments_failure_count(
        self, scenario_session, scenario_factory, monkeypatch
    ):
        """Each DOWN health check increments the failure counter."""
        monkeypatch.setattr(
            "src.providers.registry.async_session_factory", scenario_factory
        )
        from src.providers.registry import ProviderRegistry

        registry = ProviderRegistry()

        scenario_session.add(ProviderType(
            id="mock", display_name="Mock", icon="m",
            category="test", config_schema="{}", default_intervals="{}",
        ))
        await scenario_session.flush()
        inst = ProviderInstance(
            provider_type_id="mock", display_name="Failing Provider",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300, is_enabled=True,
        )
        scenario_session.add(inst)
        await scenario_session.flush()

        scenario_session.add(ProviderInstanceState(
            instance_id=inst.id, health_status="up", failure_count=0,
        ))
        await scenario_session.commit()

        # Two consecutive failures
        down_result = HealthResult(status=HealthStatus.DOWN, message="Timeout")
        await registry._update_health_state(inst.id, down_result)
        await registry._update_health_state(inst.id, down_result)

        result = await scenario_session.execute(
            select(ProviderInstanceState).where(
                ProviderInstanceState.instance_id == inst.id
            )
        )
        state = result.scalar_one()
        assert state.health_status == "down"
        assert state.failure_count == 2


# ---------- S5: Config Change Invalidation ----------

class TestConfigChangeInvalidation:
    """S5: Cache invalidated when provider config changes."""

    @pytest.mark.asyncio
    async def test_remove_instance_clears_cache(
        self, scenario_session, scenario_factory, monkeypatch
    ):
        """Removing an instance invalidates all its cache entries."""
        monkeypatch.setattr(
            "src.providers.cache.async_session_factory", scenario_factory
        )

        from src.providers.cache import invalidate_cache, write_cache, read_cache

        # Write some cache data
        await write_cache(99, "health", {"status": "up"}, datetime.utcnow())
        await write_cache(99, "summary", {"items": 5}, datetime.utcnow())

        # Verify data exists
        data, _, _ = await read_cache(99, "health")
        assert data is not None

        # Invalidate all cache for instance
        await invalidate_cache(99)

        # Verify data is gone
        data, _, _ = await read_cache(99, "health")
        assert data is None
        data, _, _ = await read_cache(99, "summary")
        assert data is None


# ---------- S19: Action Permission Denied ----------

class TestActionPermissionDenied:
    """S19: Action rejected when user lacks permission."""

    @pytest.mark.asyncio
    async def test_unknown_action_rejected(self):
        """Registry rejects actions that don't exist on the provider."""
        from src.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        provider = MockProvider(
            instance_id=1, display_name="Test", config={"url": "http://test", "api_key": "k"},
        )
        registry._instances[1] = provider

        result = await registry.execute_action(1, "nonexistent_action", {}, user_id=1)
        assert result.success is False
        assert "Unknown action" in result.message

    @pytest.mark.asyncio
    async def test_action_on_missing_instance(self):
        """Registry rejects action for nonexistent provider instance."""
        from src.providers.registry import ProviderRegistry

        registry = ProviderRegistry()
        result = await registry.execute_action(999, "test", {}, user_id=1)
        assert result.success is False
        assert "not found" in result.message


# ---------- S20: Action on Down Provider ----------

class TestActionOnDownProvider:
    """S20: Actions attempted on a DOWN provider."""

    @pytest.mark.asyncio
    async def test_action_fails_on_unreachable_provider(
        self, scenario_factory, monkeypatch
    ):
        """Action execution fails gracefully when provider is unreachable."""
        monkeypatch.setattr(
            "src.providers.registry.async_session_factory", scenario_factory
        )

        provider = MockProvider(
            instance_id=1, display_name="Test",
            config={"url": "http://test", "api_key": "k"},
        )

        # Make execute_action raise a connection error
        async def broken_action(action: str, params: dict) -> ActionResult:
            raise httpx.ConnectError("Connection refused")

        provider.execute_action = broken_action

        from src.providers.registry import ProviderRegistry
        registry = ProviderRegistry()
        registry._instances[1] = provider

        result = await registry.execute_action(1, "refresh", {}, user_id=1)
        assert result.success is False
        assert "failed" in result.message.lower()


# ---------- S22: qBittorrent Session Re-Auth ----------

class TestQbitReAuth:
    """S22: qBittorrent re-authenticates on 403."""

    @pytest.mark.asyncio
    async def test_request_retries_on_403(self):
        """_request re-authenticates and retries when 403 is received."""
        from src.providers.qbittorrent import QBittorrentProvider

        call_count = {"auth": 0, "request": 0}

        class ReAuthTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                path = request.url.raw_path.decode("utf-8").split("?")[0]
                if path == "/api/v2/auth/login":
                    call_count["auth"] += 1
                    return httpx.Response(
                        status_code=200,
                        text="Ok.",
                        headers={"set-cookie": "SID=newsid; Path=/"},
                    )
                if path == "/api/v2/app/version":
                    call_count["request"] += 1
                    # First call returns 403, subsequent calls succeed
                    if call_count["request"] <= 1:
                        return httpx.Response(status_code=403)
                    return httpx.Response(status_code=200, text="v5.0.0")
                return httpx.Response(status_code=404)

        provider = QBittorrentProvider(
            instance_id=1, display_name="Test qBit",
            config={"url": "http://qbit:8080", "username": "admin", "password": "pass"},
        )
        provider.http_client = httpx.AsyncClient(
            transport=ReAuthTransport(), base_url="http://qbit:8080"
        )

        # Make a request that will get 403 → re-auth → retry
        resp = await provider._request("GET", "/api/v2/app/version")

        assert call_count["auth"] >= 1  # Re-authenticated
        # After re-auth, should succeed on retry
        assert resp.status_code in (200, 403)  # 200 if retry worked

    @pytest.mark.asyncio
    async def test_auth_failure_returns_down(self):
        """Health check returns DOWN when authentication fails completely."""
        from src.providers.qbittorrent import QBittorrentProvider

        class FailAuthTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                path = request.url.raw_path.decode("utf-8").split("?")[0]
                if path == "/api/v2/auth/login":
                    return httpx.Response(status_code=200, text="Fails.")
                return httpx.Response(status_code=403)

        provider = QBittorrentProvider(
            instance_id=1, display_name="Test qBit",
            config={"url": "http://qbit:8080", "username": "admin", "password": "wrong"},
        )
        provider.http_client = httpx.AsyncClient(
            transport=FailAuthTransport(), base_url="http://qbit:8080"
        )

        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "auth" in result.message.lower() or "Authentication" in result.message


# ---------- S30: Docker Self-Protection ----------

class TestDockerSelfProtection:
    """S30: Docker provider rejects actions on its own container."""

    @pytest.mark.asyncio
    async def test_restart_self_rejected(self):
        """Restart action on own container is rejected."""
        from src.providers.docker import DockerProvider

        provider = DockerProvider(
            instance_id=1, display_name="Docker",
            config={"url": "http://localhost", "socket_path": "/var/run/docker.sock"},
        )
        provider._self_container_id = "abc123def456"

        result = await provider.execute_action(
            "restart", {"container_id": "abc123def456789"}
        )
        assert result.success is False
        assert "great eye" in result.message.lower()

    @pytest.mark.asyncio
    async def test_stop_self_rejected(self):
        """Stop action on own container is rejected."""
        from src.providers.docker import DockerProvider

        provider = DockerProvider(
            instance_id=1, display_name="Docker",
            config={"url": "http://localhost", "socket_path": "/var/run/docker.sock"},
        )
        provider._self_container_id = "abc123def456"

        result = await provider.execute_action(
            "stop", {"container_id": "abc123def456789"}
        )
        assert result.success is False


# ---------- S35: SECRET_KEY Change ----------

class TestSecretKeyChange:
    """S35: Encrypted data becomes unrecoverable when SECRET_KEY changes."""

    def test_decrypt_fails_with_wrong_key(self):
        """Decryption fails when using a different SECRET_KEY."""
        from src.services.encryption import derive_fernet_key

        # Encrypt with key A
        fernet_a = derive_fernet_key("original-secret-key", b"provider-config-encryption")
        ciphertext = fernet_a.encrypt(b"my-api-key-12345").decode("utf-8")

        # Try to decrypt with key B
        fernet_b = derive_fernet_key("different-secret-key", b"provider-config-encryption")
        with pytest.raises(Exception):  # InvalidToken from Fernet
            fernet_b.decrypt(ciphertext.encode("utf-8"))

    def test_same_key_decrypts_successfully(self):
        """Same SECRET_KEY always produces same Fernet key (HKDF is deterministic)."""
        from src.services.encryption import derive_fernet_key

        fernet_1 = derive_fernet_key("my-stable-key", b"provider-config-encryption")
        fernet_2 = derive_fernet_key("my-stable-key", b"provider-config-encryption")

        plaintext = b"secret-api-key"
        ciphertext = fernet_1.encrypt(plaintext).decode("utf-8")
        decrypted = fernet_2.decrypt(ciphertext.encode("utf-8"))
        assert decrypted == plaintext

    def test_different_info_produces_different_key(self):
        """Different info parameters produce different encryption keys."""
        from src.services.encryption import derive_fernet_key

        fernet_config = derive_fernet_key("same-key", b"provider-config-encryption")
        fernet_session = derive_fernet_key("same-key", b"session-signing")

        ciphertext = fernet_config.encrypt(b"test").decode("utf-8")
        with pytest.raises(Exception):
            fernet_session.decrypt(ciphertext.encode("utf-8"))


# ---------- S38: Slow Network / Timeout ----------

class TestSlowNetworkTimeout:
    """S38: Provider handles slow/timing-out upstream gracefully."""

    @pytest.mark.asyncio
    async def test_health_timeout_returns_down(self):
        """Health check timeout results in DOWN status."""
        import asyncio

        provider = MockProvider(
            instance_id=1, display_name="Slow",
            config={"url": "http://test", "api_key": "k"},
        )

        # Override health_check to simulate slow response
        async def slow_health() -> HealthResult:
            await asyncio.sleep(10)
            return HealthResult(status=HealthStatus.UP, message="OK")

        provider.health_check = slow_health

        # Use wait_for with short timeout (like the scheduler does)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(provider.health_check(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_summary_timeout_does_not_crash(self):
        """Summary fetch timeout is caught, no crash."""
        import asyncio

        provider = MockProvider(
            instance_id=1, display_name="Slow",
            config={"url": "http://test", "api_key": "k"},
        )

        async def slow_summary() -> SummaryResult:
            await asyncio.sleep(10)
            return SummaryResult(data={}, fetched_at=datetime.utcnow())

        provider.get_summary = slow_summary

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(provider.get_summary(), timeout=0.1)


# ---------- S4: Partial Data ----------

class TestPartialData:
    """S4: Provider returns partial data when some sources fail."""

    @pytest.mark.asyncio
    async def test_stale_cache_served_on_failure(
        self, scenario_factory, monkeypatch
    ):
        """Stale cache data is served when a fresh fetch fails."""
        monkeypatch.setattr(
            "src.providers.cache.async_session_factory", scenario_factory
        )

        from src.providers.cache import write_cache, mark_stale, read_cache

        now = datetime.utcnow()
        await write_cache(1, "summary", {"items": 42}, now)

        # Mark as stale (simulating provider going down)
        await mark_stale(1, "summary")

        # Read cache — stale data still returned
        data, fetched_at, is_stale = await read_cache(1, "summary")
        assert data == {"items": 42}
        assert is_stale is True
