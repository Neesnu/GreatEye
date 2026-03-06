"""Shared route helpers for sidebar context and layout."""

from typing import Any

from src.models.user import User
from src.providers.registry import registry
from src.services.layout import (
    get_grouped_sidebar,
    merge_with_available,
    parse_layout,
)


async def add_sidebar_context(context: dict[str, Any], user: User) -> None:
    """Add grouped sidebar data to a template context dict.

    Populates: sidebar_groups, ungrouped_instances, sidebar_instances (flat fallback).
    """
    sidebar_instances = await registry.get_sidebar_instances()
    layout = parse_layout(user.layout_json)
    layout = merge_with_available(
        layout, [i["instance_id"] for i in sidebar_instances]
    )
    grouped = get_grouped_sidebar(layout, sidebar_instances)
    context["sidebar_groups"] = grouped["groups"]
    context["ungrouped_instances"] = grouped["ungrouped"]
    # Keep flat list as fallback for templates that haven't been updated yet
    context["sidebar_instances"] = sidebar_instances
