"""Generic provider detail view and action routes.

Works for all provider types — templates are resolved by type_id.
"""

from pathlib import Path

import json as json_module

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from markupsafe import escape
from fastapi.templating import Jinja2Templates

from src.auth.dependencies import get_current_user, require_permission
from src.models.user import User
from src.providers.registry import registry
from src.routes._helpers import add_sidebar_context
from src.utils.formatting import format_bytes, format_speed, format_eta

logger = structlog.get_logger()

router = APIRouter(prefix="/providers", tags=["providers"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Register Jinja2 filters for detail templates
templates.env.filters["format_bytes"] = format_bytes
templates.env.filters["format_speed"] = format_speed
templates.env.filters["format_eta"] = format_eta


def _json_attr_filter(value: object) -> str:
    """JSON-encode for use in HTML attributes.

    Returns a plain str (not Markup) so Jinja2 auto-escaping will convert
    double quotes to &quot;, keeping the attribute value safe.  The browser
    decodes &quot; back to " when the form is submitted.
    """
    return json_module.dumps(value, separators=(",", ":"))


templates.env.filters["json_attr"] = _json_attr_filter


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

    await add_sidebar_context(context, user)
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
            f'<div class="toast toast--success">{escape(result.message)}</div>',
            headers={"HX-Trigger": "actionComplete"},
        )
    else:
        return HTMLResponse(
            f'<div class="toast toast--error">{escape(result.message)}</div>',
            status_code=400,
        )


# ------------------------------------------------------------------
# Manual Import
# ------------------------------------------------------------------


@router.get("/{instance_id}/manual-import", response_class=HTMLResponse)
async def manual_import_preview(
    request: Request,
    instance_id: int,
    download_id: str = Query(...),
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Render manual import preview for a download."""
    provider = registry.get_instance(instance_id)
    if provider is None:
        return HTMLResponse("Provider not found", status_code=404)

    meta = provider.meta()
    import_perm = f"{meta.type_id}.import"
    permission_keys = getattr(user, "permission_keys", set())
    if import_perm not in permission_keys and "system.admin" not in permission_keys:
        return HTMLResponse("Forbidden", status_code=403)

    if not hasattr(provider, "_fetch_manual_import_preview"):
        return HTMLResponse("Manual import not supported for this provider", status_code=400)

    raw_files = await provider._fetch_manual_import_preview(download_id)
    if not raw_files:
        return HTMLResponse(
            '<div class="toast toast--error">No importable files found for this download</div>',
        )

    files = [provider._normalize_manual_import_file(f) for f in raw_files]

    detail_result = await registry.get_detail(instance_id)
    detail = detail_result.data if detail_result else {}

    context = {
        "request": request,
        "instance_id": instance_id,
        "type_id": meta.type_id,
        "download_id": download_id,
        "files": files,
        "detail": detail,
        "permission_keys": permission_keys,
    }

    template_name = f"detail/{meta.type_id}/manual_import.html"
    return HTMLResponse(
        templates.get_template(template_name).render(context)
    )


@router.post("/{instance_id}/manual-import", response_class=HTMLResponse)
async def manual_import_execute(
    request: Request,
    instance_id: int,
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Execute manual import with user-confirmed file list."""
    provider = registry.get_instance(instance_id)
    if provider is None:
        return HTMLResponse("Provider not found", status_code=404)

    meta = provider.meta()
    import_perm = f"{meta.type_id}.import"
    permission_keys = getattr(user, "permission_keys", set())
    if import_perm not in permission_keys and "system.admin" not in permission_keys:
        return HTMLResponse("Forbidden", status_code=403)

    form = await request.form()
    params = dict(form)

    result = await registry.execute_action(instance_id, "manual_import", params, user.id)

    if result.success:
        return HTMLResponse(
            f'<div class="toast toast--success">{escape(result.message)}</div>',
            headers={
                "HX-Trigger": "actionComplete",
                "HX-Redirect": f"/providers/{instance_id}?tab=queue",
            },
        )
    else:
        return HTMLResponse(
            f'<div class="toast toast--error">{escape(result.message)}</div>',
            status_code=400,
        )


@router.get("/{instance_id}/manual-import/episodes", response_class=HTMLResponse)
async def manual_import_episodes(
    request: Request,
    instance_id: int,
    series_id: int = Query(...),
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Return episode <option> list for a series (Sonarr dependent select)."""
    provider = registry.get_instance(instance_id)
    if provider is None:
        return HTMLResponse("Provider not found", status_code=404)

    meta = provider.meta()
    if meta.type_id != "sonarr":
        return HTMLResponse("Not applicable", status_code=400)

    import_perm = f"{meta.type_id}.import"
    permission_keys = getattr(user, "permission_keys", set())
    if import_perm not in permission_keys and "system.admin" not in permission_keys:
        return HTMLResponse("Forbidden", status_code=403)

    provider._ensure_headers()
    try:
        response = await provider.http_client.get(
            f"{provider.api_base}/episode",
            params={"seriesId": series_id},
        )
        if response.status_code != 200:
            return HTMLResponse('<option value="">Error loading episodes</option>')

        episodes = response.json()
        options_html = ""
        for ep in sorted(episodes, key=lambda e: (e.get("seasonNumber", 0), e.get("episodeNumber", 0))):
            ep_id = ep.get("id", "")
            s = ep.get("seasonNumber", 0)
            e = ep.get("episodeNumber", 0)
            title = ep.get("title", "")
            label = f"S{s:02d}E{e:02d}"
            if title:
                label += f" - {title}"
            options_html += f'<option value="{ep_id}">{label}</option>\n'

        return HTMLResponse(options_html)
    except Exception:
        return HTMLResponse('<option value="">Error loading episodes</option>')
