import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.database import get_db
from src.models.session import Session
from src.models.user import User
from src.schemas.layout import (
    CardOrderUpdate,
    GroupCreate,
    GroupRename,
    HiddenUpdate,
    SidebarUpdate,
)
from src.services.layout import (
    SidebarGroup,
    new_group_id,
    parse_layout,
    serialize_layout,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.post("/delivery-mode")
async def set_delivery_mode(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Toggle between SSE and batch delivery mode."""
    form = await request.form()
    mode = form.get("mode", "sse")
    if mode not in ("sse", "batch"):
        mode = "sse"

    session_id = request.cookies.get("session_id")
    if session_id:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.delivery_mode = mode
            await db.commit()

    logger.info("delivery_mode_changed", user_id=user.id, mode=mode)

    return HTMLResponse("", headers={"HX-Refresh": "true"})


# ---------------------------------------------------------------------------
# Layout CRUD
# ---------------------------------------------------------------------------


@router.get("/layout")
async def get_layout(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return the current user's layout JSON."""
    layout = parse_layout(user.layout_json)
    return JSONResponse(content={
        "sidebar_groups": [
            {
                "id": g.id,
                "name": g.name,
                "collapsed": g.collapsed,
                "instance_ids": g.instance_ids,
            }
            for g in layout.sidebar_groups
        ],
        "card_order": layout.card_order,
        "hidden_instance_ids": layout.hidden_instance_ids,
    })


@router.put("/layout/sidebar")
async def update_sidebar(
    body: SidebarUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update sidebar groups (from drag-and-drop reorder)."""
    layout = parse_layout(user.layout_json)
    layout.sidebar_groups = [
        SidebarGroup(
            id=g.id,
            name=g.name,
            collapsed=g.collapsed,
            instance_ids=g.instance_ids,
        )
        for g in body.groups
    ]
    user.layout_json = serialize_layout(layout)
    await db.commit()
    logger.info("layout_sidebar_updated", user_id=user.id)
    return JSONResponse(content={"ok": True})


@router.put("/layout/card-order")
async def update_card_order(
    body: CardOrderUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update card order (from drag-and-drop reorder)."""
    layout = parse_layout(user.layout_json)
    layout.card_order = body.card_order
    user.layout_json = serialize_layout(layout)
    await db.commit()
    logger.info("layout_card_order_updated", user_id=user.id)
    return JSONResponse(content={"ok": True})


@router.post("/layout/sidebar/groups")
async def create_group(
    body: GroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Create a new empty sidebar group."""
    layout = parse_layout(user.layout_json)
    gid = new_group_id()
    layout.sidebar_groups.append(SidebarGroup(id=gid, name=body.name))
    user.layout_json = serialize_layout(layout)
    await db.commit()
    logger.info("layout_group_created", user_id=user.id, group_id=gid)
    return JSONResponse(content={"ok": True, "group_id": gid}, status_code=201)


@router.put("/layout/sidebar/groups/{group_id}")
async def rename_group(
    group_id: str,
    body: GroupRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Rename a sidebar group."""
    layout = parse_layout(user.layout_json)
    for g in layout.sidebar_groups:
        if g.id == group_id:
            g.name = body.name
            user.layout_json = serialize_layout(layout)
            await db.commit()
            logger.info("layout_group_renamed", user_id=user.id, group_id=group_id)
            return JSONResponse(content={"ok": True})
    return JSONResponse(content={"error": "Group not found"}, status_code=404)


@router.delete("/layout/sidebar/groups/{group_id}")
async def delete_group(
    group_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Delete a sidebar group. Items move to ungrouped."""
    layout = parse_layout(user.layout_json)
    layout.sidebar_groups = [g for g in layout.sidebar_groups if g.id != group_id]
    user.layout_json = serialize_layout(layout)
    await db.commit()
    logger.info("layout_group_deleted", user_id=user.id, group_id=group_id)
    return JSONResponse(content={"ok": True})


@router.put("/layout/sidebar/collapse/{group_id}")
async def toggle_collapse(
    group_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Toggle collapse state of a sidebar group."""
    layout = parse_layout(user.layout_json)
    for g in layout.sidebar_groups:
        if g.id == group_id:
            g.collapsed = not g.collapsed
            user.layout_json = serialize_layout(layout)
            await db.commit()
            return JSONResponse(content={"ok": True, "collapsed": g.collapsed})
    return JSONResponse(content={"error": "Group not found"}, status_code=404)


@router.put("/layout/hidden")
async def update_hidden(
    body: HiddenUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update the list of hidden instance IDs."""
    layout = parse_layout(user.layout_json)
    layout.hidden_instance_ids = body.hidden_instance_ids
    user.layout_json = serialize_layout(layout)
    await db.commit()
    logger.info("layout_hidden_updated", user_id=user.id)
    return JSONResponse(content={"ok": True})
