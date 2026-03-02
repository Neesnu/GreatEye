"""Generic provider detail view and action routes.

Works for all provider types — templates are resolved by type_id.
"""

from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.auth.dependencies import get_current_user, require_permission
from src.models.user import User
from src.providers.registry import registry
from src.utils.formatting import format_bytes, format_speed, format_eta

logger = structlog.get_logger()

router = APIRouter(prefix="/providers", tags=["providers"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Register Jinja2 filters for detail templates
templates.env.filters["format_bytes"] = format_bytes
templates.env.filters["format_speed"] = format_speed
templates.env.filters["format_eta"] = format_eta


@router.get("/{instance_id}", response_class=HTMLResponse)
async def provider_detail(
    request: Request,
    instance_id: int,
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Render the detail view for a provider instance."""
    provider = registry.get_instance(instance_id)
    if provider is None:
        return HTMLResponse("Provider not found", status_code=404)

    meta = provider.meta()
    view_perm = f"{meta.type_id}.view"
    permission_keys = getattr(user, "permission_keys", set())
    if view_perm not in permission_keys and "system.admin" not in permission_keys:
        return HTMLResponse("Forbidden", status_code=403)

    # Fetch detail data (from cache or fresh)
    detail_result = await registry.get_detail(instance_id)
    detail = detail_result.data if detail_result else {}

    context = {
        "request": request,
        "user": user,
        "instance_id": instance_id,
        "instance": {
            "instance_id": instance_id,
            "display_name": provider.display_name,
            "type_id": meta.type_id,
            "type_display_name": meta.display_name,
        },
        "detail": detail,
        "actions": provider.get_actions(),
        "permission_keys": permission_keys,
    }

    # Resolve type-specific detail template
    detail_template = f"detail/{meta.type_id}/page.html"

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            templates.get_template(detail_template).render(context)
        )

    context["sidebar_instances"] = await registry.get_sidebar_instances()
    return templates.TemplateResponse(
        "pages/provider_detail.html",
        {**context, "detail_template": detail_template},
    )


@router.post("/{instance_id}/actions/{action}", response_class=HTMLResponse)
async def provider_action(
    request: Request,
    instance_id: int,
    action: str,
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Execute a provider action."""
    provider = registry.get_instance(instance_id)
    if provider is None:
        return HTMLResponse("Provider not found", status_code=404)

    # Check action permission
    actions = provider.get_actions()
    action_def = next((a for a in actions if a.key == action), None)
    if action_def is None:
        return HTMLResponse("Unknown action", status_code=400)

    permission_keys = getattr(user, "permission_keys", set())
    if action_def.permission not in permission_keys and "system.admin" not in permission_keys:
        return HTMLResponse("Forbidden", status_code=403)

    # Collect params from form
    form = await request.form()
    params = dict(form)

    result = await registry.execute_action(instance_id, action, params, user.id)

    if result.success:
        return HTMLResponse(
            f'<div class="toast toast--success">{result.message}</div>',
            headers={"HX-Trigger": "actionComplete"},
        )
    else:
        return HTMLResponse(
            f'<div class="toast toast--error">{result.message}</div>',
            status_code=400,
        )
