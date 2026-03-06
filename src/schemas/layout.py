"""Pydantic schemas for layout API request validation."""

from pydantic import BaseModel, Field


class SidebarGroupIn(BaseModel):
    """A single sidebar group as received from the client."""

    id: str
    name: str
    collapsed: bool = False
    instance_ids: list[int] = Field(default_factory=list)


class SidebarUpdate(BaseModel):
    """Full sidebar update from drag-and-drop reorder."""

    groups: list[SidebarGroupIn]
    ungrouped_ids: list[int] = Field(default_factory=list)


class CardOrderUpdate(BaseModel):
    """Card order update from drag-and-drop reorder."""

    card_order: list[int]


class GroupCreate(BaseModel):
    """Create a new sidebar group."""

    name: str = Field(min_length=1, max_length=50)


class GroupRename(BaseModel):
    """Rename a sidebar group."""

    name: str = Field(min_length=1, max_length=50)


class HiddenUpdate(BaseModel):
    """Update the hidden instance list."""

    hidden_instance_ids: list[int]
