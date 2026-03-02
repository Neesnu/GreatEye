import importlib
import json
import pkgutil
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from src.database import async_session_factory
from src.models.provider import (
    ProviderActionLog,
    ProviderCache,
    ProviderInstance,
    ProviderInstanceState,
    ProviderType,
)
from src.models.role import Permission, Role, RolePermission
from src.providers.base import (
    ActionResult,
    BaseProvider,
    DetailResult,
    HealthResult,
    HealthStatus,
    ProviderMeta,
    SummaryResult,
)
from src.providers.cache import invalidate_cache, read_cache, write_cache
from src.providers.event_bus import Event, event_bus
from src.providers.scheduler import scheduler
from src.services.encryption import decrypt
from src.utils.validation import validate_action_params, validate_provider_url

logger = structlog.get_logger()


class ProviderRegistry:
    """Manages provider type discovery and instance lifecycle."""

    def __init__(self) -> None:
        self._provider_classes: dict[str, type[BaseProvider]] = {}
        self._instances: dict[int, BaseProvider] = {}

    async def discover_and_register(self) -> dict[str, type[BaseProvider]]:
        """Scan src/providers/ for BaseProvider subclasses, register types and permissions."""
        providers_path = Path(__file__).parent
        discovered: dict[str, type[BaseProvider]] = {}

        for _importer, module_name, _is_pkg in pkgutil.iter_modules([str(providers_path)]):
            if module_name in ("base", "arr_base", "registry", "scheduler", "cache", "event_bus"):
                continue
            try:
                module = importlib.import_module(f"src.providers.{module_name}")
            except Exception as e:
                logger.error("provider_import_failed", module=module_name, error=str(e))
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseProvider)
                    and attr is not BaseProvider
                    and getattr(attr, "__module__", "") == f"src.providers.{module_name}"
                ):
                    try:
                        meta = attr.meta()
                        discovered[meta.type_id] = attr
                    except Exception as e:
                        logger.error(
                            "provider_meta_failed",
                            class_name=attr.__name__,
                            error=str(e),
                        )

        self._provider_classes = discovered

        if discovered:
            await self._register_types(discovered)
            await self._register_permissions(discovered)
            logger.info(
                "providers_discovered",
                count=len(discovered),
                types=list(discovered.keys()),
            )
        else:
            logger.info("no_providers_discovered")

        return discovered

    async def _register_types(
        self, discovered: dict[str, type[BaseProvider]]
    ) -> None:
        """Upsert provider_types table with discovered provider metadata."""
        async with async_session_factory() as session:
            for type_id, provider_class in discovered.items():
                meta = provider_class.meta()
                result = await session.execute(
                    select(ProviderType).where(ProviderType.id == type_id)
                )
                existing = result.scalar_one_or_none()

                schema_json = json.dumps(meta.config_schema)
                intervals_json = json.dumps(meta.default_intervals)

                if existing:
                    existing.display_name = meta.display_name
                    existing.icon = meta.icon
                    existing.category = meta.category
                    existing.config_schema = schema_json
                    existing.default_intervals = intervals_json
                else:
                    session.add(ProviderType(
                        id=type_id,
                        display_name=meta.display_name,
                        icon=meta.icon,
                        category=meta.category,
                        config_schema=schema_json,
                        default_intervals=intervals_json,
                    ))

            await session.commit()

    async def _register_permissions(
        self, discovered: dict[str, type[BaseProvider]]
    ) -> None:
        """Register provider permissions and auto-assign to roles by category."""
        async with async_session_factory() as session:
            # Load roles
            result = await session.execute(select(Role))
            roles = {r.name: r for r in result.scalars().all()}
            admin_role = roles.get("admin")
            user_role = roles.get("user")
            viewer_role = roles.get("viewer")

            for type_id, provider_class in discovered.items():
                meta = provider_class.meta()
                for perm_def in meta.permissions:
                    # Upsert permission
                    result = await session.execute(
                        select(Permission).where(Permission.key == perm_def.key)
                    )
                    perm = result.scalar_one_or_none()
                    if perm is None:
                        perm = Permission(
                            key=perm_def.key,
                            display_name=perm_def.display_name,
                            description=perm_def.description,
                            provider_type=type_id,
                            category=perm_def.category,
                        )
                        session.add(perm)
                        await session.flush()
                        logger.debug("permission_registered", key=perm_def.key)

                    # Auto-assign based on category
                    role_targets: list[Role] = []
                    if admin_role:
                        role_targets.append(admin_role)
                    if perm_def.category in ("read", "action") and user_role:
                        role_targets.append(user_role)
                    if perm_def.category == "read" and viewer_role:
                        role_targets.append(viewer_role)

                    for role in role_targets:
                        result = await session.execute(
                            select(RolePermission).where(
                                RolePermission.role_id == role.id,
                                RolePermission.permission_id == perm.id,
                            )
                        )
                        if result.scalar_one_or_none() is None:
                            session.add(RolePermission(
                                role_id=role.id, permission_id=perm.id
                            ))

            await session.commit()

    async def initialize_instances(self) -> None:
        """Load enabled instances from DB, instantiate, run initial health, schedule polling."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ProviderInstance).where(ProviderInstance.is_enabled == True)
            )
            instances = result.scalars().all()

        for db_instance in instances:
            await self._start_instance(db_instance)

        logger.info("instances_initialized", count=len(self._instances))

    async def _start_instance(self, db_instance: ProviderInstance) -> None:
        """Instantiate a single provider, create HTTP client, run health check, schedule."""
        type_id = db_instance.provider_type_id
        provider_class = self._provider_classes.get(type_id)
        if provider_class is None:
            logger.warning(
                "provider_type_not_found",
                type_id=type_id,
                instance_id=db_instance.id,
            )
            return

        config = self._decrypt_config(db_instance.config, provider_class.meta())

        try:
            provider = provider_class(
                instance_id=db_instance.id,
                display_name=db_instance.display_name,
                config=config,
            )
        except Exception as e:
            logger.error(
                "provider_instantiation_failed",
                type_id=type_id,
                instance_id=db_instance.id,
                error=str(e),
            )
            return

        # Create preconfigured httpx client
        base_url = config.get("url", "")
        provider.http_client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            headers={"User-Agent": "GreatEye/1.0"},
            follow_redirects=False,
        )

        self._instances[db_instance.id] = provider

        # Run initial health check (non-blocking)
        try:
            health = await provider.health_check()
            await self._update_health_state(db_instance.id, health)
            await write_cache(
                db_instance.id,
                "health",
                {
                    "status": health.status.value,
                    "message": health.message,
                    "response_time_ms": health.response_time_ms,
                },
                datetime.utcnow(),
            )
        except Exception as e:
            logger.error(
                "initial_health_check_failed",
                instance_id=db_instance.id,
                error=str(e),
            )

        # Schedule polling
        await scheduler.schedule_instance(
            provider,
            health_interval=db_instance.health_interval,
            summary_interval=db_instance.summary_interval,
        )

        logger.info(
            "instance_started",
            provider_type=type_id,
            instance_id=db_instance.id,
            display_name=db_instance.display_name,
        )

    def _decrypt_config(
        self, encrypted_config: str, meta: ProviderMeta
    ) -> dict[str, Any]:
        """Decrypt secret fields in the provider config."""
        config = json.loads(encrypted_config)
        secret_fields = {
            f["key"]
            for f in meta.config_schema.get("fields", [])
            if f.get("type") == "secret"
        }
        for key in secret_fields:
            if key in config and config[key]:
                try:
                    config[key] = decrypt(config[key])
                except Exception as e:
                    logger.error(
                        "config_decrypt_failed",
                        field=key,
                        error=str(e),
                    )
                    config[key] = ""
        return config

    async def _update_health_state(
        self, instance_id: int, health: HealthResult
    ) -> None:
        """Update the provider_instance_state table."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(ProviderInstanceState).where(
                    ProviderInstanceState.instance_id == instance_id
                )
            )
            state = result.scalar_one_or_none()

            now = datetime.utcnow()
            if state is None:
                state = ProviderInstanceState(
                    instance_id=instance_id,
                    health_status=health.status.value,
                    health_message=health.message,
                    last_health_check=now,
                    failure_count=0,
                )
                if health.status == HealthStatus.UP:
                    state.last_successful = now
                session.add(state)
            else:
                was_down = state.health_status in ("down", "degraded")
                state.health_status = health.status.value
                state.health_message = health.message
                state.last_health_check = now

                if health.status == HealthStatus.UP:
                    state.last_successful = now
                    if was_down:
                        state.failure_count = 0
                        logger.info(
                            "provider_recovered",
                            instance_id=instance_id,
                        )
                elif health.status in (HealthStatus.DOWN, HealthStatus.DEGRADED):
                    state.failure_count += 1

            await session.commit()

    # ----- Public API -----

    def get_instance(self, instance_id: int) -> BaseProvider | None:
        """Get a live provider instance by ID."""
        return self._instances.get(instance_id)

    def get_all_instances(self) -> list[BaseProvider]:
        """Get all active provider instances."""
        return list(self._instances.values())

    async def get_sidebar_instances(self) -> list[dict[str, Any]]:
        """Get lightweight instance data for sidebar rendering."""
        items: list[dict[str, Any]] = []
        async with async_session_factory() as session:
            for provider in self._instances.values():
                meta = provider.meta()
                result = await session.execute(
                    select(ProviderInstanceState).where(
                        ProviderInstanceState.instance_id == provider.instance_id
                    )
                )
                state = result.scalar_one_or_none()
                items.append({
                    "instance_id": provider.instance_id,
                    "display_name": provider.display_name,
                    "type_id": meta.type_id,
                    "health_status": state.health_status if state else "unknown",
                    "health_message": state.health_message if state else "",
                })
        return items

    def get_provider_class(self, type_id: str) -> type[BaseProvider] | None:
        """Get a provider class by type ID."""
        return self._provider_classes.get(type_id)

    def get_registered_types(self) -> dict[str, type[BaseProvider]]:
        """Get all registered provider type classes."""
        return dict(self._provider_classes)

    async def get_health(self, instance_id: int) -> HealthResult:
        """Get cached health for an instance."""
        data, fetched_at, is_stale = await read_cache(instance_id, "health")
        if data is None:
            return HealthResult(status=HealthStatus.UNKNOWN, message="No data")
        return HealthResult(
            status=HealthStatus(data["status"]),
            message=data["message"],
            response_time_ms=data.get("response_time_ms"),
        )

    async def get_summary(self, instance_id: int) -> SummaryResult | None:
        """Get cached summary for an instance."""
        data, fetched_at, is_stale = await read_cache(instance_id, "summary")
        if data is None:
            return None
        return SummaryResult(data=data, fetched_at=fetched_at)

    async def get_detail(self, instance_id: int) -> DetailResult | None:
        """Get detail data — from cache if fresh, otherwise fetch live."""
        provider = self._instances.get(instance_id)
        if provider is None:
            return None

        data, fetched_at, is_stale = await read_cache(instance_id, "detail")

        # Get detail cache TTL from DB
        async with async_session_factory() as session:
            result = await session.execute(
                select(ProviderInstance).where(
                    ProviderInstance.id == instance_id
                )
            )
            db_instance = result.scalar_one_or_none()
            ttl = db_instance.detail_cache_ttl if db_instance else 300

        if data is not None and not is_stale:
            from src.providers.cache import is_within_ttl
            if is_within_ttl(fetched_at, ttl):
                return DetailResult(data=data, fetched_at=fetched_at)

        # Fetch fresh
        try:
            detail = await provider.get_detail()
            await write_cache(instance_id, "detail", detail.data, detail.fetched_at)
            return detail
        except Exception as e:
            logger.error(
                "detail_fetch_failed",
                instance_id=instance_id,
                error=str(e),
            )
            if data is not None:
                return DetailResult(data=data, fetched_at=fetched_at)
            return None

    async def execute_action(
        self,
        instance_id: int,
        action: str,
        params: dict[str, Any],
        user_id: int,
    ) -> ActionResult:
        """Execute an action, validate params, log it, invalidate cache if needed."""
        provider = self._instances.get(instance_id)
        if provider is None:
            return ActionResult(success=False, message="Provider instance not found")

        type_id = provider.meta().type_id

        # Find action definition and validate params
        actions = provider.get_actions()
        action_def = next((a for a in actions if a.key == action), None)
        if action_def is None:
            return ActionResult(success=False, message=f"Unknown action: {action}")

        # Validate params per H5
        valid, msg = validate_action_params(params, action_def.params_schema)
        if not valid:
            return ActionResult(success=False, message=msg)

        # Execute
        try:
            result = await provider.execute_action(action, params)
        except Exception as e:
            logger.error(
                "action_execution_failed",
                provider_type=type_id,
                instance_id=instance_id,
                action=action,
                error=str(e),
            )
            result = ActionResult(success=False, message=f"Action failed: {str(e)}")

        # Log the action (sanitize params — strip secret-looking values)
        sanitized_params = {
            k: "***" if any(s in k.lower() for s in ("key", "password", "token", "secret")) else v
            for k, v in params.items()
        }
        async with async_session_factory() as session:
            session.add(ProviderActionLog(
                instance_id=instance_id,
                user_id=user_id,
                action=action,
                params=json.dumps(sanitized_params),
                result="success" if result.success else "failure",
                result_message=result.message[:500] if result.message else None,
            ))
            await session.commit()

        logger.info(
            "action_executed",
            provider_type=type_id,
            instance_id=instance_id,
            action=action,
            user_id=user_id,
            success=result.success,
        )

        # Invalidate cache if action says to
        if result.success and result.invalidate_cache:
            await invalidate_cache(instance_id, "summary")
            await invalidate_cache(instance_id, "detail")
            await event_bus.publish(Event(
                name=f"summary:{instance_id}",
                data={"instance_id": instance_id, "tier": "summary", "action": action},
            ))

        return result

    async def get_dashboard_state(self) -> dict[int, dict[str, Any]]:
        """Get all health + summary data for all instances (batch endpoint)."""
        state: dict[int, dict[str, Any]] = {}
        for instance_id, provider in self._instances.items():
            health_data, _, _ = await read_cache(instance_id, "health")
            summary_data, summary_at, summary_stale = await read_cache(
                instance_id, "summary"
            )
            meta = provider.meta()
            state[instance_id] = {
                "instance_id": instance_id,
                "display_name": provider.display_name,
                "type_id": meta.type_id,
                "type_display_name": meta.display_name,
                "icon": meta.icon,
                "category": meta.category,
                "health": health_data,
                "summary": summary_data,
                "summary_fetched_at": summary_at,
                "summary_stale": summary_stale,
            }
        return state

    async def add_instance(
        self, type_id: str, display_name: str, config: dict[str, Any]
    ) -> int | None:
        """Create a new provider instance. Returns instance_id or None on failure."""
        provider_class = self._provider_classes.get(type_id)
        if provider_class is None:
            return None

        meta = provider_class.meta()

        # SSRF validation on URL field
        url = config.get("url", "")
        if url:
            valid, msg = validate_provider_url(url)
            if not valid:
                logger.warning("ssrf_blocked", url=url, message=msg)
                return None

        # Encrypt secret fields
        from src.services.encryption import encrypt
        stored_config = dict(config)
        secret_fields = {
            f["key"]
            for f in meta.config_schema.get("fields", [])
            if f.get("type") == "secret"
        }
        for key in secret_fields:
            if key in stored_config and stored_config[key]:
                stored_config[key] = encrypt(stored_config[key])

        defaults = meta.default_intervals
        async with async_session_factory() as session:
            db_instance = ProviderInstance(
                provider_type_id=type_id,
                display_name=display_name,
                config=json.dumps(stored_config),
                health_interval=defaults.get("health_seconds", 30),
                summary_interval=defaults.get("summary_seconds", 60),
                detail_cache_ttl=defaults.get("detail_cache_seconds", 300),
            )
            session.add(db_instance)
            await session.commit()
            await session.refresh(db_instance)
            instance_id = db_instance.id

        # Start the instance
        async with async_session_factory() as session:
            result = await session.execute(
                select(ProviderInstance).where(ProviderInstance.id == instance_id)
            )
            db_instance = result.scalar_one()

        await self._start_instance(db_instance)
        return instance_id

    async def remove_instance(self, instance_id: int) -> None:
        """Stop and remove a provider instance."""
        provider = self._instances.pop(instance_id, None)
        if provider:
            if provider.http_client:
                await provider.http_client.aclose()
            await provider.cleanup()
            await scheduler.unschedule_instance(instance_id)

        await invalidate_cache(instance_id)
        logger.info("instance_removed", instance_id=instance_id)

    async def shutdown(self) -> None:
        """Shut down all instances and the scheduler."""
        await scheduler.stop_all()
        for instance_id, provider in self._instances.items():
            if provider.http_client:
                await provider.http_client.aclose()
            await provider.cleanup()
        self._instances.clear()
        logger.info("registry_shutdown")


# Singleton instance
registry = ProviderRegistry()
