import asyncio
import json
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.database import get_db
from src.models.session import Session
from src.models.user import User
from src.providers.cache import read_cache
from src.providers.event_bus import Event, event_bus
from src.providers.registry import registry
from src.routes._helpers import add_sidebar_context
from src.services.layout import get_ordered_instances, merge_with_available, parse_layout

logger = structlog.get_logger()

router = APIRouter(tags=["dashboard"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the full dashboard page with all cards from cache."""
    # Get delivery mode from session
    session_id = request.cookies.get("session_id")
    delivery_mode = "sse"
    if session_id:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if session:
            delivery_mode = session.delivery_mode or "sse"

    # Build card data for all instances the user can see, apply layout ordering
    instances = await _get_visible_instances(user)
    layout = parse_layout(user.layout_json)
    available_ids = [i["instance_id"] for i in instances]
    layout = merge_with_available(layout, available_ids)
    instances = get_ordered_instances(layout, instances)

    context = {
        "request": request,
        "user": user,
        "instances": instances,
        "delivery_mode": delivery_mode,
    }

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            templates.get_template("partials/dashboard_content.html").render(context)
        )

    await add_sidebar_context(context, user)
    return templates.TemplateResponse("pages/dashboard.html", context)


@router.get("/dashboard/cards", response_class=HTMLResponse)
async def dashboard_cards(
    request: Request,
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Return all summary cards as HTML (batch polling endpoint)."""
    instances = await _get_visible_instances(user)
    layout = parse_layout(user.layout_json)
    available_ids = [i["instance_id"] for i in instances]
    layout = merge_with_available(layout, available_ids)
    instances = get_ordered_instances(layout, instances)
    return templates.TemplateResponse("partials/dashboard_cards.html", {
        "request": request,
        "instances": instances,
    })


@router.get("/dashboard/stream")
async def dashboard_stream(
    request: Request,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE event stream. Pushes card HTML when cache data changes."""
    permission_keys = getattr(user, "permission_keys", set())

    async def event_generator():
        sub_id, queue = await event_bus.subscribe()
        try:
            # Send initial state dump on connect
            instances = await _get_visible_instances(user)
            for inst in instances:
                card_html = templates.get_template(
                    "partials/card_inner.html"
                ).render({"instance": inst})
                yield _format_sse(f"summary:{inst['instance_id']}", card_html)

                health_html = templates.get_template(
                    "partials/health_dot.html"
                ).render({"instance": inst})
                yield _format_sse(f"health:{inst['instance_id']}", health_html)

            # Stream updates as they arrive
            while True:
                try:
                    event: Event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
                    continue

                instance_id = event.data.get("instance_id")
                if instance_id is None:
                    continue

                # Check if user can see this instance
                provider = registry.get_instance(instance_id)
                if provider is None:
                    continue
                view_perm = f"{provider.meta().type_id}.view"
                if view_perm not in permission_keys and "system.admin" not in permission_keys:
                    continue

                # Re-read cache and render
                inst = await _build_instance_data(provider)

                if event.name.startswith("summary:"):
                    card_html = templates.get_template(
                        "partials/card_inner.html"
                    ).render({"instance": inst})
                    yield _format_sse(event.name, card_html)
                elif event.name.startswith("health:"):
                    health_html = templates.get_template(
                        "partials/health_dot.html"
                    ).render({"instance": inst})
                    yield _format_sse(event.name, health_html)

        except asyncio.CancelledError:
            pass
        finally:
            await event_bus.unsubscribe(sub_id)
            logger.debug("sse_disconnected", subscriber_id=sub_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event_name: str, data: str) -> str:
    """Format an SSE message with event name and data."""
    # SSE data lines cannot contain newlines, so join multi-line HTML
    lines = data.replace("\r\n", "\n").split("\n")
    data_lines = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event_name}\n{data_lines}\n\n"


async def _get_visible_instances(user: User) -> list[dict]:
    """Get all provider instances visible to this user, with cached data."""
    permission_keys = getattr(user, "permission_keys", set())
    instances = []

    for provider in registry.get_all_instances():
        meta = provider.meta()
        view_perm = f"{meta.type_id}.view"
        if view_perm not in permission_keys and "system.admin" not in permission_keys:
            continue

        inst = await _build_instance_data(provider)
        instances.append(inst)

    return instances


async def _build_instance_data(provider) -> dict:
    """Build the template data dict for a provider instance."""
    meta = provider.meta()

    health_data, _, _ = await read_cache(provider.instance_id, "health")
    summary_data, summary_at, summary_stale = await read_cache(
        provider.instance_id, "summary"
    )

    health_status = "unknown"
    health_message = ""
    if health_data:
        health_status = health_data.get("status", "unknown")
        health_message = health_data.get("message", "")

    # Check if a type-specific card template exists
    card_path = f"cards/{meta.type_id}.html"
    try:
        templates.env.get_template(card_path)
        card_template = card_path
    except Exception:
        card_template = None

    return {
        "instance_id": provider.instance_id,
        "display_name": provider.display_name,
        "type_id": meta.type_id,
        "type_display_name": meta.display_name,
        "icon": meta.icon,
        "category": meta.category,
        "health_status": health_status,
        "health_message": health_message,
        "summary": summary_data,
        "summary_fetched_at": summary_at,
        "summary_stale": summary_stale,
        "card_template": card_template,
    }
