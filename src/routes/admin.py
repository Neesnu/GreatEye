import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user, require_permission
from src.auth.local import hash_password
from src.database import get_db
from src.models.auth import PasswordResetToken, PlexApprovedUser
from src.models.provider import ProviderInstance, ProviderInstanceState, ProviderType
from src.models.role import Permission, Role, RolePermission
from src.models.session import Session
from src.models.user import User
from src.providers.registry import registry
from src.services.encryption import encrypt, decrypt

logger = structlog.get_logger()

router = APIRouter(prefix="/admin", tags=["admin"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


async def _render(request: Request, template: str, context: dict) -> HTMLResponse:
    """Render full page or partial depending on HX-Request."""
    if request.headers.get("HX-Request"):
        return HTMLResponse(templates.get_template(template).render(context))
    context["sidebar_instances"] = await registry.get_sidebar_instances()
    return templates.TemplateResponse(
        "base.html", {**context, "content_template": template}
    )


# ---------------------------------------------------------------------------
# Provider Management
# ---------------------------------------------------------------------------

@router.get("/providers")
async def list_providers(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """List all provider instances with health status."""
    result = await db.execute(
        select(ProviderInstance).order_by(ProviderInstance.sort_order, ProviderInstance.id)
    )
    instances = result.scalars().all()

    # Load health state for each
    instance_data: list[dict[str, Any]] = []
    for inst in instances:
        state_result = await db.execute(
            select(ProviderInstanceState).where(
                ProviderInstanceState.instance_id == inst.id
            )
        )
        state = state_result.scalar_one_or_none()
        instance_data.append({
            "instance": inst,
            "health_status": state.health_status if state else "unknown",
            "health_message": state.health_message if state else "",
        })

    # Load provider types for the "Add" dropdown
    type_result = await db.execute(select(ProviderType).order_by(ProviderType.display_name))
    provider_types = type_result.scalars().all()

    return await _render(request, "admin/providers.html", {
        "request": request,
        "user": user,
        "instances": instance_data,
        "provider_types": provider_types,
    })


@router.get("/providers/new")
async def new_provider_form(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show form for creating a new provider instance."""
    type_id = request.query_params.get("type_id", "")

    type_result = await db.execute(select(ProviderType).order_by(ProviderType.display_name))
    provider_types = type_result.scalars().all()

    # Get schema for selected type
    config_schema: dict = {}
    selected_type = None
    if type_id:
        for pt in provider_types:
            if pt.id == type_id:
                selected_type = pt
                config_schema = json.loads(pt.config_schema)
                break

    return await _render(request, "admin/provider_form.html", {
        "request": request,
        "user": user,
        "provider_types": provider_types,
        "selected_type": selected_type,
        "config_schema": config_schema,
        "instance": None,
        "config_values": {},
        "form_action": "/admin/providers",
        "form_method": "post",
    })


@router.post("/providers")
async def create_provider(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Create a new provider instance."""
    form = await request.form()
    type_id = form.get("type_id", "").strip()
    display_name = form.get("display_name", "").strip()

    if not type_id or not display_name:
        return HTMLResponse(
            '<div class="toast toast--error">Type and name are required</div>',
            status_code=400,
        )

    # Get provider type schema
    provider_class = registry.get_provider_class(type_id)
    if provider_class is None:
        return HTMLResponse(
            '<div class="toast toast--error">Unknown provider type</div>',
            status_code=400,
        )

    meta = provider_class.meta()

    # Build config from form fields
    config: dict[str, Any] = {}
    for field in meta.config_schema.get("fields", []):
        key = field["key"]
        value = form.get(f"config_{key}", "").strip()
        if value:
            config[key] = value
        elif field.get("default") is not None:
            config[key] = str(field["default"])

    # Add instance via registry (handles encryption and startup)
    instance_id = await registry.add_instance(type_id, display_name, config)
    if instance_id is None:
        return HTMLResponse(
            '<div class="toast toast--error">Failed to create instance (check URL/config)</div>',
            status_code=400,
        )

    logger.info(
        "provider_created",
        type_id=type_id,
        instance_id=instance_id,
        created_by=user.username,
    )

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/providers"},
    )


@router.get("/providers/{instance_id}/edit")
async def edit_provider_form(
    request: Request,
    instance_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show form for editing an existing provider instance."""
    result = await db.execute(
        select(ProviderInstance).where(ProviderInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        return HTMLResponse("Not found", status_code=404)

    # Load type info
    type_result = await db.execute(
        select(ProviderType).where(ProviderType.id == instance.provider_type_id)
    )
    selected_type = type_result.scalar_one_or_none()
    config_schema = json.loads(selected_type.config_schema) if selected_type else {}

    # Decrypt config for display (mask secrets)
    config_values = json.loads(instance.config)
    secret_fields = {
        f["key"]
        for f in config_schema.get("fields", [])
        if f.get("type") == "secret"
    }
    for key in secret_fields:
        if key in config_values and config_values[key]:
            try:
                # Decrypt to show it's set, but mask it
                decrypt(config_values[key])
                config_values[key] = ""  # Clear so field shows placeholder
            except Exception:
                config_values[key] = ""

    return await _render(request, "admin/provider_form.html", {
        "request": request,
        "user": user,
        "provider_types": [],
        "selected_type": selected_type,
        "config_schema": config_schema,
        "instance": instance,
        "config_values": config_values,
        "form_action": f"/admin/providers/{instance_id}",
        "form_method": "put",
    })


@router.put("/providers/{instance_id}")
async def update_provider(
    request: Request,
    instance_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Update an existing provider instance."""
    result = await db.execute(
        select(ProviderInstance).where(ProviderInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        return HTMLResponse("Not found", status_code=404)

    form = await request.form()
    display_name = form.get("display_name", "").strip()
    if display_name:
        instance.display_name = display_name

    # Get provider type schema for secret detection
    provider_class = registry.get_provider_class(instance.provider_type_id)
    if provider_class is None:
        return HTMLResponse("Provider type not found", status_code=400)

    meta = provider_class.meta()
    secret_fields = {
        f["key"]
        for f in meta.config_schema.get("fields", [])
        if f.get("type") == "secret"
    }

    # Merge config: keep existing encrypted values for empty secret fields
    existing_config = json.loads(instance.config)
    new_config: dict[str, Any] = {}
    for field in meta.config_schema.get("fields", []):
        key = field["key"]
        value = form.get(f"config_{key}", "").strip()
        if key in secret_fields:
            if value:
                # New value provided — encrypt it
                new_config[key] = encrypt(value)
            else:
                # Keep existing encrypted value
                new_config[key] = existing_config.get(key, "")
        else:
            new_config[key] = value if value else existing_config.get(key, "")

    instance.config = json.dumps(new_config)

    # Update intervals if provided
    health_interval = form.get("health_interval")
    summary_interval = form.get("summary_interval")
    detail_cache_ttl = form.get("detail_cache_ttl")
    if health_interval:
        instance.health_interval = int(health_interval)
    if summary_interval:
        instance.summary_interval = int(summary_interval)
    if detail_cache_ttl:
        instance.detail_cache_ttl = int(detail_cache_ttl)

    # Commit config changes before restarting
    await db.commit()

    # Restart the instance with new config
    await registry.remove_instance(instance_id)

    # Re-read from DB (committed) and restart
    result = await db.execute(
        select(ProviderInstance).where(ProviderInstance.id == instance_id)
    )
    refreshed = result.scalar_one()
    if refreshed.is_enabled:
        await registry._start_instance(refreshed)

    logger.info(
        "provider_updated",
        instance_id=instance_id,
        updated_by=user.username,
    )

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/providers"},
    )


@router.delete("/providers/{instance_id}")
async def delete_provider(
    instance_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Delete a provider instance."""
    result = await db.execute(
        select(ProviderInstance).where(ProviderInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        return HTMLResponse("Not found", status_code=404)

    await registry.remove_instance(instance_id)

    # Delete state and cache rows, then the instance
    await db.execute(
        delete(ProviderInstanceState).where(
            ProviderInstanceState.instance_id == instance_id
        )
    )
    await db.execute(
        delete(ProviderInstance).where(ProviderInstance.id == instance_id)
    )

    logger.info(
        "provider_deleted",
        instance_id=instance_id,
        deleted_by=user.username,
    )

    return HTMLResponse(status_code=200)


@router.post("/providers/{instance_id}/test")
async def test_provider(
    instance_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Test provider connection via validate_config."""
    provider = registry.get_instance(instance_id)
    if provider is None:
        # Not running — try to instantiate temporarily
        result = await db.execute(
            select(ProviderInstance).where(ProviderInstance.id == instance_id)
        )
        db_instance = result.scalar_one_or_none()
        if db_instance is None:
            return HTMLResponse(
                '<div class="test-result test-result--error">Instance not found</div>',
                status_code=404,
            )
        return HTMLResponse(
            '<div class="test-result test-result--error">Instance not running — enable it first</div>',
        )

    try:
        ok, msg = await provider.validate_config()
    except Exception as e:
        ok, msg = False, str(e)

    if ok:
        return HTMLResponse(
            f'<div class="test-result test-result--success">{msg}</div>',
        )
    return HTMLResponse(
        f'<div class="test-result test-result--error">{msg}</div>',
    )


@router.post("/providers/{instance_id}/toggle")
async def toggle_provider(
    instance_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Enable or disable a provider instance."""
    result = await db.execute(
        select(ProviderInstance).where(ProviderInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        return HTMLResponse("Not found", status_code=404)

    instance.is_enabled = not instance.is_enabled

    if instance.is_enabled:
        # Start the instance
        await db.flush()
        result = await db.execute(
            select(ProviderInstance).where(ProviderInstance.id == instance_id)
        )
        refreshed = result.scalar_one()
        await registry._start_instance(refreshed)
        logger.info("provider_enabled", instance_id=instance_id, enabled_by=user.username)
    else:
        # Stop the instance
        await registry.remove_instance(instance_id)
        logger.info("provider_disabled", instance_id=instance_id, disabled_by=user.username)

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/providers"},
    )


@router.post("/providers/sort")
async def sort_providers(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Update dashboard sort order for provider instances."""
    form = await request.form()
    # Expect order[] = [id1, id2, ...] from sortable list
    order = form.getlist("order[]")
    if not order:
        # Try JSON body fallback
        try:
            body = await request.json()
            order = body.get("order", [])
        except Exception:
            pass

    for idx, instance_id in enumerate(order):
        result = await db.execute(
            select(ProviderInstance).where(ProviderInstance.id == int(instance_id))
        )
        instance = result.scalar_one_or_none()
        if instance:
            instance.sort_order = idx

    logger.info("provider_sort_updated", updated_by=user.username)
    return HTMLResponse(status_code=200)


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@router.get("/users")
async def list_users(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """List all users with role and auth info."""
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()

    # Load roles for dropdown
    role_result = await db.execute(select(Role).order_by(Role.name))
    roles = role_result.scalars().all()

    return await _render(request, "admin/users.html", {
        "request": request,
        "user": user,
        "users": users,
        "roles": roles,
    })


@router.get("/users/new")
async def new_user_form(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show form for creating a new user."""
    role_result = await db.execute(select(Role).order_by(Role.name))
    roles = role_result.scalars().all()

    return await _render(request, "admin/user_form.html", {
        "request": request,
        "user": user,
        "roles": roles,
        "edit_user": None,
    })


@router.post("/users")
async def create_user(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Create a new local user."""
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "").strip()
    role_id = form.get("role_id", "")

    if not username or not password:
        return HTMLResponse(
            '<div class="toast toast--error">Username and password are required</div>',
            status_code=400,
        )

    if len(password) < 8:
        return HTMLResponse(
            '<div class="toast toast--error">Password must be at least 8 characters</div>',
            status_code=400,
        )

    # Check uniqueness
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none() is not None:
        return HTMLResponse(
            '<div class="toast toast--error">Username already exists</div>',
            status_code=400,
        )

    if not role_id:
        result = await db.execute(select(Role).where(Role.name == "user"))
        default_role = result.scalar_one()
        role_id = default_role.id

    new_user = User(
        username=username,
        password_hash=hash_password(password),
        auth_method="local",
        role_id=int(role_id),
        is_active=True,
    )
    db.add(new_user)

    logger.info("user_created", username=username, created_by=user.username)

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/users"},
    )


@router.get("/users/{user_id}/edit")
async def edit_user_form(
    request: Request,
    user_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show form for editing a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    edit_user = result.scalar_one_or_none()
    if edit_user is None:
        return HTMLResponse("Not found", status_code=404)

    role_result = await db.execute(select(Role).order_by(Role.name))
    roles = role_result.scalars().all()

    return await _render(request, "admin/user_form.html", {
        "request": request,
        "user": user,
        "roles": roles,
        "edit_user": edit_user,
    })


@router.put("/users/{user_id}")
async def update_user(
    request: Request,
    user_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Update an existing user."""
    result = await db.execute(select(User).where(User.id == user_id))
    edit_user = result.scalar_one_or_none()
    if edit_user is None:
        return HTMLResponse("Not found", status_code=404)

    form = await request.form()

    # Update role
    role_id = form.get("role_id", "")
    if role_id:
        edit_user.role_id = int(role_id)

    # Update active status
    is_active = form.get("is_active")
    if is_active is not None:
        edit_user.is_active = is_active == "on" or is_active == "true"

    # Update password if provided
    new_password = form.get("password", "").strip()
    if new_password:
        if len(new_password) < 8:
            return HTMLResponse(
                '<div class="toast toast--error">Password must be at least 8 characters</div>',
                status_code=400,
            )
        edit_user.password_hash = hash_password(new_password)

    logger.info("user_updated", user_id=user_id, updated_by=user.username)

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/users"},
    )


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Delete a user. Cannot delete yourself."""
    if user_id == user.id:
        return HTMLResponse(
            '<div class="toast toast--error">Cannot delete your own account</div>',
            status_code=400,
        )

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        return HTMLResponse("Not found", status_code=404)

    # Delete user sessions first
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))

    logger.info("user_deleted", user_id=user_id, deleted_by=user.username)

    return HTMLResponse(status_code=200)


@router.post("/users/{user_id}/force-reset")
async def toggle_force_reset(
    user_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Toggle the force_reset flag on a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        return HTMLResponse("Not found", status_code=404)

    target.force_reset = not target.force_reset

    logger.info(
        "force_reset_toggled",
        user_id=user_id,
        force_reset=target.force_reset,
        toggled_by=user.username,
    )

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/users"},
    )


# ---------------------------------------------------------------------------
# Role Management
# ---------------------------------------------------------------------------

@router.get("/roles")
async def list_roles(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """List all roles with their permissions."""
    result = await db.execute(select(Role).order_by(Role.id))
    roles = result.scalars().all()

    # Load all permissions grouped by provider_type
    perm_result = await db.execute(
        select(Permission).order_by(Permission.provider_type, Permission.key)
    )
    all_permissions = perm_result.scalars().all()

    # Group permissions by provider type
    perm_groups: dict[str, list] = {}
    for perm in all_permissions:
        perm_groups.setdefault(perm.provider_type, []).append(perm)

    return await _render(request, "admin/roles.html", {
        "request": request,
        "user": user,
        "roles": roles,
        "perm_groups": perm_groups,
    })


@router.get("/roles/{role_id}/edit")
async def edit_role_form(
    request: Request,
    role_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show form for editing a role's permissions."""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        return HTMLResponse("Not found", status_code=404)

    perm_result = await db.execute(
        select(Permission).order_by(Permission.provider_type, Permission.key)
    )
    all_permissions = perm_result.scalars().all()

    perm_groups: dict[str, list] = {}
    for perm in all_permissions:
        perm_groups.setdefault(perm.provider_type, []).append(perm)

    role_perm_keys = {p.key for p in role.permissions}

    return await _render(request, "admin/role_form.html", {
        "request": request,
        "user": user,
        "role": role,
        "perm_groups": perm_groups,
        "role_perm_keys": role_perm_keys,
    })


@router.put("/roles/{role_id}")
async def update_role(
    request: Request,
    role_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Update a role's permissions."""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        return HTMLResponse("Not found", status_code=404)

    form = await request.form()

    # Update role name/description if custom role
    if not role.is_system:
        new_name = form.get("name", "").strip()
        if new_name:
            role.name = new_name
        new_desc = form.get("description", "").strip()
        if new_desc:
            role.description = new_desc

    # Update permissions — form sends permission_ids[] as checked checkboxes
    selected_perm_ids = [int(pid) for pid in form.getlist("permission_ids")]

    # Clear existing permissions
    await db.execute(
        delete(RolePermission).where(RolePermission.role_id == role_id)
    )
    await db.flush()

    # Add selected permissions
    for perm_id in selected_perm_ids:
        db.add(RolePermission(role_id=role_id, permission_id=perm_id))

    logger.info(
        "role_updated",
        role_id=role_id,
        permission_count=len(selected_perm_ids),
        updated_by=user.username,
    )

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/roles"},
    )


@router.post("/roles")
async def create_role(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Create a custom role."""
    form = await request.form()
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()

    if not name:
        return HTMLResponse(
            '<div class="toast toast--error">Role name is required</div>',
            status_code=400,
        )

    # Check uniqueness
    result = await db.execute(select(Role).where(Role.name == name))
    if result.scalar_one_or_none() is not None:
        return HTMLResponse(
            '<div class="toast toast--error">Role name already exists</div>',
            status_code=400,
        )

    new_role = Role(
        name=name,
        description=description or None,
        is_system=False,
    )
    db.add(new_role)

    logger.info("role_created", name=name, created_by=user.username)

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/roles"},
    )


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Delete a custom role. System roles cannot be deleted."""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        return HTMLResponse("Not found", status_code=404)

    if role.is_system:
        return HTMLResponse(
            '<div class="toast toast--error">System roles cannot be deleted</div>',
            status_code=400,
        )

    # Check no users have this role
    user_count_result = await db.execute(
        select(sa_func.count()).select_from(User).where(User.role_id == role_id)
    )
    user_count = user_count_result.scalar()
    if user_count > 0:
        return HTMLResponse(
            f'<div class="toast toast--error">Cannot delete — {user_count} users have this role</div>',
            status_code=400,
        )

    # Delete permissions associations, then the role
    await db.execute(
        delete(RolePermission).where(RolePermission.role_id == role_id)
    )
    await db.execute(delete(Role).where(Role.id == role_id))

    logger.info("role_deleted", role_id=role_id, deleted_by=user.username)

    return HTMLResponse(status_code=200)


# ---------------------------------------------------------------------------
# System Settings
# ---------------------------------------------------------------------------

@router.get("/settings")
async def settings_page(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Show system settings page."""
    # Gather system info
    user_count_result = await db.execute(select(sa_func.count()).select_from(User))
    user_count = user_count_result.scalar()

    instance_count_result = await db.execute(
        select(sa_func.count()).select_from(ProviderInstance)
    )
    instance_count = instance_count_result.scalar()

    role_count_result = await db.execute(select(sa_func.count()).select_from(Role))
    role_count = role_count_result.scalar()

    perm_count_result = await db.execute(select(sa_func.count()).select_from(Permission))
    perm_count = perm_count_result.scalar()

    provider_type_count = len(registry.get_registered_types())

    return await _render(request, "admin/settings.html", {
        "request": request,
        "user": user,
        "stats": {
            "users": user_count,
            "instances": instance_count,
            "roles": role_count,
            "permissions": perm_count,
            "provider_types": provider_type_count,
        },
    })


# ---------------------------------------------------------------------------
# Plex Approved Users
# ---------------------------------------------------------------------------

@router.get("/plex-users")
async def list_plex_users(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """List all approved Plex usernames."""
    result = await db.execute(select(PlexApprovedUser))
    approved = result.scalars().all()
    return await _render(request, "admin/plex_users.html", {
        "request": request,
        "user": user,
        "approved_users": approved,
    })


@router.post("/plex-users")
async def add_plex_user(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Add a Plex username to the approved list."""
    form = await request.form()
    plex_username = form.get("plex_username", "").strip()
    role_id = form.get("role_id")

    if not plex_username:
        return HTMLResponse(
            '<p class="form-error">Plex username is required</p>',
            status_code=400,
        )

    # Check if already approved
    result = await db.execute(
        select(PlexApprovedUser).where(
            PlexApprovedUser.plex_username == plex_username
        )
    )
    if result.scalar_one_or_none() is not None:
        return HTMLResponse(
            '<p class="form-error">User already approved</p>',
            status_code=400,
        )

    # Default to "user" role if not specified
    if not role_id:
        result = await db.execute(select(Role).where(Role.name == "user"))
        default_role = result.scalar_one()
        role_id = default_role.id

    approved = PlexApprovedUser(
        plex_username=plex_username,
        default_role_id=int(role_id),
        approved_by=user.id,
    )
    db.add(approved)

    logger.info(
        "plex_user_approved",
        plex_username=plex_username,
        approved_by=user.username,
    )

    return HTMLResponse(
        status_code=200,
        headers={"HX-Redirect": "/admin/plex-users"},
    )


@router.delete("/plex-users/{approved_id}")
async def remove_plex_user(
    approved_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Remove a Plex username from the approved list."""
    result = await db.execute(
        select(PlexApprovedUser).where(PlexApprovedUser.id == approved_id)
    )
    approved = result.scalar_one_or_none()
    if approved is None:
        return HTMLResponse(status_code=404, content="Not found")

    logger.info(
        "plex_user_removed",
        plex_username=approved.plex_username,
        removed_by=user.username,
    )

    await db.execute(
        delete(PlexApprovedUser).where(PlexApprovedUser.id == approved_id)
    )

    return HTMLResponse(status_code=200)


# ---------------------------------------------------------------------------
# Password Reset Queue
# ---------------------------------------------------------------------------

@router.get("/resets")
async def list_resets(
    request: Request,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """List pending (unused, unexpired) password reset tokens."""
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.utcnow(),
        )
    )
    resets = result.scalars().all()
    return await _render(request, "admin/resets.html", {
        "request": request,
        "user": user,
        "resets": resets,
    })


@router.delete("/resets/{reset_id}")
async def cancel_reset(
    reset_id: int,
    user: User = Depends(require_permission("system.admin")),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Cancel a pending password reset token."""
    await db.execute(
        delete(PasswordResetToken).where(PasswordResetToken.id == reset_id)
    )
    logger.info("reset_cancelled", reset_id=reset_id, cancelled_by=user.username)
    return HTMLResponse(status_code=200)
