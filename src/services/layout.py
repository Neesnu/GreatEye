"""Per-user layout service: sidebar groups, card ordering, hidden instances."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class SidebarGroup:
    """A named group of provider instances in the sidebar."""

    id: str
    name: str
    collapsed: bool = False
    instance_ids: list[int] = field(default_factory=list)


@dataclass
class UserLayout:
    """Per-user layout preferences."""

    sidebar_groups: list[SidebarGroup] = field(default_factory=list)
    card_order: list[int] = field(default_factory=list)
    hidden_instance_ids: list[int] = field(default_factory=list)


def parse_layout(raw: str | None) -> UserLayout:
    """Parse a layout JSON string into a UserLayout, graceful on None/invalid."""
    if not raw:
        return UserLayout()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("layout_parse_failed", raw_length=len(raw) if raw else 0)
        return UserLayout()

    groups = []
    for g in data.get("sidebar_groups", []):
        groups.append(SidebarGroup(
            id=g.get("id", str(uuid.uuid4())),
            name=g.get("name", "Unnamed"),
            collapsed=bool(g.get("collapsed", False)),
            instance_ids=[int(i) for i in g.get("instance_ids", [])],
        ))

    return UserLayout(
        sidebar_groups=groups,
        card_order=[int(i) for i in data.get("card_order", [])],
        hidden_instance_ids=[int(i) for i in data.get("hidden_instance_ids", [])],
    )


def serialize_layout(layout: UserLayout) -> str:
    """Serialize a UserLayout to a JSON string."""
    return json.dumps({
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
    }, separators=(",", ":"))


def merge_with_available(layout: UserLayout, available_ids: list[int]) -> UserLayout:
    """Reconcile a layout with the current set of available instance IDs.

    - Prune instance IDs that no longer exist from groups, card_order, hidden.
    - Append newly discovered IDs to ungrouped + end of card_order.
    """
    available = set(available_ids)

    # Collect all IDs currently tracked in the layout
    tracked: set[int] = set()
    for group in layout.sidebar_groups:
        group.instance_ids = [i for i in group.instance_ids if i in available]
        tracked.update(group.instance_ids)

    layout.card_order = [i for i in layout.card_order if i in available]
    tracked.update(layout.card_order)

    layout.hidden_instance_ids = [i for i in layout.hidden_instance_ids if i in available]
    tracked.update(layout.hidden_instance_ids)

    # Append new IDs (not yet in any group, card_order, or hidden)
    new_ids = sorted(available - tracked)
    layout.card_order.extend(i for i in new_ids if i not in layout.card_order)

    return layout


def get_ordered_instances(
    layout: UserLayout,
    instances: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reorder instances by card_order, excluding hidden. Unordered go to end."""
    hidden = set(layout.hidden_instance_ids)
    visible = [i for i in instances if i["instance_id"] not in hidden]

    if not layout.card_order:
        return visible

    order_map = {iid: idx for idx, iid in enumerate(layout.card_order)}
    return sorted(visible, key=lambda i: order_map.get(i["instance_id"], 999999))


def get_grouped_sidebar(
    layout: UserLayout,
    sidebar_instances: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build grouped sidebar data from layout + available instances.

    Returns:
        {
            "groups": [
                {"id": ..., "name": ..., "collapsed": ..., "items": [instance_dicts]},
                ...
            ],
            "ungrouped": [instance_dicts],
        }
    """
    hidden = set(layout.hidden_instance_ids)
    by_id: dict[int, dict[str, Any]] = {
        i["instance_id"]: i for i in sidebar_instances if i["instance_id"] not in hidden
    }

    grouped_ids: set[int] = set()
    groups: list[dict[str, Any]] = []

    for sg in layout.sidebar_groups:
        items = []
        for iid in sg.instance_ids:
            inst = by_id.get(iid)
            if inst:
                items.append(inst)
                grouped_ids.add(iid)
        groups.append({
            "id": sg.id,
            "name": sg.name,
            "collapsed": sg.collapsed,
            "instances": items,
        })

    ungrouped = [inst for iid, inst in by_id.items() if iid not in grouped_ids]

    return {"groups": groups, "ungrouped": ungrouped}


def new_group_id() -> str:
    """Generate a new unique group ID."""
    return str(uuid.uuid4())
