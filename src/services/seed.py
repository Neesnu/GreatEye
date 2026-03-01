import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import async_session_factory
from src.models.role import Permission, Role, RolePermission

logger = structlog.get_logger()

SYSTEM_ROLES = [
    {"name": "admin", "description": "Full access to all features", "is_system": True},
    {"name": "user", "description": "View + safe actions", "is_system": True},
    {"name": "viewer", "description": "Read-only access", "is_system": True},
]

SYSTEM_PERMISSIONS = [
    {
        "key": "system.admin",
        "display_name": "System Administration",
        "description": "Full access to admin settings, users, and roles",
        "provider_type": "system",
        "category": "admin",
    },
]


async def seed_roles(session: AsyncSession) -> None:
    """Create the three system roles if they don't already exist."""
    for role_data in SYSTEM_ROLES:
        result = await session.execute(
            select(Role).where(Role.name == role_data["name"])
        )
        if result.scalar_one_or_none() is None:
            session.add(Role(**role_data))
            logger.info("role_seeded", role=role_data["name"])


async def seed_permissions(session: AsyncSession) -> None:
    """Create system permissions and assign them to the admin role."""
    for perm_data in SYSTEM_PERMISSIONS:
        result = await session.execute(
            select(Permission).where(Permission.key == perm_data["key"])
        )
        perm = result.scalar_one_or_none()
        if perm is None:
            perm = Permission(**perm_data)
            session.add(perm)
            await session.flush()
            logger.info("permission_seeded", key=perm_data["key"])

        # Ensure admin role has this permission
        result = await session.execute(select(Role).where(Role.name == "admin"))
        admin_role = result.scalar_one_or_none()
        if admin_role:
            result = await session.execute(
                select(RolePermission).where(
                    RolePermission.role_id == admin_role.id,
                    RolePermission.permission_id == perm.id,
                )
            )
            if result.scalar_one_or_none() is None:
                session.add(RolePermission(
                    role_id=admin_role.id, permission_id=perm.id
                ))
                logger.info(
                    "permission_assigned",
                    role="admin",
                    permission=perm_data["key"],
                )


async def run_seed() -> None:
    """Run all seed functions."""
    async with async_session_factory() as session:
        await seed_roles(session)
        await session.flush()
        await seed_permissions(session)
        await session.commit()
    logger.info("seed_complete")
