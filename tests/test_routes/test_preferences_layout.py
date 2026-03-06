"""Tests for layout CRUD API endpoints in preferences routes."""

import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.database import get_db
from src.models.user import User
from src.routes.preferences import router as preferences_router
from src.services.layout import new_group_id, parse_layout, serialize_layout, SidebarGroup, UserLayout


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_user(layout_json: str | None = None) -> MagicMock:
    """Create a mock user for dependency injection."""
    user = MagicMock(spec=User)
    user.id = 1
    user.username = "testuser"
    user.layout_json = layout_json
    return user


@pytest_asyncio.fixture
async def test_state(db_session: AsyncSession):
    """Return a dict holding the mock user and test app."""
    user = _make_user()

    app = FastAPI()
    app.include_router(preferences_router)

    async def override_get_db():
        yield db_session

    async def override_get_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_user

    return {"app": app, "user": user}


@pytest_asyncio.fixture
async def client(test_state):
    transport = ASGITransport(app=test_state["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET /preferences/layout
# ---------------------------------------------------------------------------


class TestGetLayout:
    @pytest.mark.asyncio
    async def test_empty_layout(self, client, test_state):
        test_state["user"].layout_json = None
        resp = await client.get("/preferences/layout")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sidebar_groups"] == []
        assert data["card_order"] == []
        assert data["hidden_instance_ids"] == []

    @pytest.mark.asyncio
    async def test_existing_layout(self, client, test_state):
        layout = UserLayout(
            sidebar_groups=[SidebarGroup(id="g1", name="Media", instance_ids=[1, 2])],
            card_order=[2, 1],
            hidden_instance_ids=[3],
        )
        test_state["user"].layout_json = serialize_layout(layout)
        resp = await client.get("/preferences/layout")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sidebar_groups"]) == 1
        assert data["sidebar_groups"][0]["name"] == "Media"
        assert data["card_order"] == [2, 1]
        assert data["hidden_instance_ids"] == [3]


# ---------------------------------------------------------------------------
# PUT /preferences/layout/sidebar
# ---------------------------------------------------------------------------


class TestUpdateSidebar:
    @pytest.mark.asyncio
    async def test_update_groups(self, client, test_state):
        resp = await client.put("/preferences/layout/sidebar", json={
            "groups": [
                {"id": "g1", "name": "Media", "collapsed": False, "instance_ids": [1, 2]},
                {"id": "g2", "name": "Infra", "collapsed": True, "instance_ids": [3]},
            ],
            "ungrouped_ids": [4],
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify persisted
        saved = parse_layout(test_state["user"].layout_json)
        assert len(saved.sidebar_groups) == 2
        assert saved.sidebar_groups[0].name == "Media"
        assert saved.sidebar_groups[1].collapsed is True


# ---------------------------------------------------------------------------
# PUT /preferences/layout/card-order
# ---------------------------------------------------------------------------


class TestUpdateCardOrder:
    @pytest.mark.asyncio
    async def test_update_order(self, client, test_state):
        resp = await client.put("/preferences/layout/card-order", json={
            "card_order": [3, 1, 2],
        })
        assert resp.status_code == 200
        saved = parse_layout(test_state["user"].layout_json)
        assert saved.card_order == [3, 1, 2]


# ---------------------------------------------------------------------------
# POST /preferences/layout/sidebar/groups
# ---------------------------------------------------------------------------


class TestCreateGroup:
    @pytest.mark.asyncio
    async def test_create(self, client, test_state):
        resp = await client.post("/preferences/layout/sidebar/groups", json={
            "name": "Network",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert "group_id" in data

        saved = parse_layout(test_state["user"].layout_json)
        assert len(saved.sidebar_groups) == 1
        assert saved.sidebar_groups[0].name == "Network"

    @pytest.mark.asyncio
    async def test_create_empty_name_rejected(self, client):
        resp = await client.post("/preferences/layout/sidebar/groups", json={
            "name": "",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_long_name_rejected(self, client):
        resp = await client.post("/preferences/layout/sidebar/groups", json={
            "name": "x" * 51,
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /preferences/layout/sidebar/groups/{group_id}
# ---------------------------------------------------------------------------


class TestRenameGroup:
    @pytest.mark.asyncio
    async def test_rename(self, client, test_state):
        # Seed a group
        layout = UserLayout(sidebar_groups=[SidebarGroup(id="g1", name="Old")])
        test_state["user"].layout_json = serialize_layout(layout)

        resp = await client.put("/preferences/layout/sidebar/groups/g1", json={
            "name": "New Name",
        })
        assert resp.status_code == 200
        saved = parse_layout(test_state["user"].layout_json)
        assert saved.sidebar_groups[0].name == "New Name"

    @pytest.mark.asyncio
    async def test_rename_not_found(self, client, test_state):
        test_state["user"].layout_json = None
        resp = await client.put("/preferences/layout/sidebar/groups/nonexistent", json={
            "name": "X",
        })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /preferences/layout/sidebar/groups/{group_id}
# ---------------------------------------------------------------------------


class TestDeleteGroup:
    @pytest.mark.asyncio
    async def test_delete(self, client, test_state):
        layout = UserLayout(sidebar_groups=[
            SidebarGroup(id="g1", name="A", instance_ids=[1]),
            SidebarGroup(id="g2", name="B", instance_ids=[2]),
        ])
        test_state["user"].layout_json = serialize_layout(layout)

        resp = await client.delete("/preferences/layout/sidebar/groups/g1")
        assert resp.status_code == 200
        saved = parse_layout(test_state["user"].layout_json)
        assert len(saved.sidebar_groups) == 1
        assert saved.sidebar_groups[0].id == "g2"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_ok(self, client, test_state):
        test_state["user"].layout_json = None
        resp = await client.delete("/preferences/layout/sidebar/groups/nope")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# PUT /preferences/layout/sidebar/collapse/{group_id}
# ---------------------------------------------------------------------------


class TestToggleCollapse:
    @pytest.mark.asyncio
    async def test_toggle_collapse(self, client, test_state):
        layout = UserLayout(sidebar_groups=[SidebarGroup(id="g1", name="X", collapsed=False)])
        test_state["user"].layout_json = serialize_layout(layout)

        resp = await client.put("/preferences/layout/sidebar/collapse/g1")
        assert resp.status_code == 200
        assert resp.json()["collapsed"] is True

        # Toggle back
        resp = await client.put("/preferences/layout/sidebar/collapse/g1")
        assert resp.status_code == 200
        assert resp.json()["collapsed"] is False

    @pytest.mark.asyncio
    async def test_collapse_not_found(self, client, test_state):
        test_state["user"].layout_json = None
        resp = await client.put("/preferences/layout/sidebar/collapse/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /preferences/layout/hidden
# ---------------------------------------------------------------------------


class TestUpdateHidden:
    @pytest.mark.asyncio
    async def test_update_hidden(self, client, test_state):
        resp = await client.put("/preferences/layout/hidden", json={
            "hidden_instance_ids": [4, 6],
        })
        assert resp.status_code == 200
        saved = parse_layout(test_state["user"].layout_json)
        assert saved.hidden_instance_ids == [4, 6]
