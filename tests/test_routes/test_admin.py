"""Tests for admin routes — provider, user, and role management."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.local import hash_password
from src.database import get_db
from src.models.provider import ProviderInstance, ProviderType
from src.models.role import Permission, Role, RolePermission
from src.models.user import User
from src.routes.admin import router as admin_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_admin_user() -> MagicMock:
    """Create a mock admin user for dependency injection."""
    user = MagicMock(spec=User)
    user.id = 1
    user.username = "admin"
    user.role_id = 1
    user.permission_keys = {"system.admin"}
    return user


def _create_test_app(db_session: AsyncSession) -> FastAPI:
    """Create a minimal FastAPI app with admin routes and overridden deps."""
    test_app = FastAPI()
    test_app.include_router(admin_router)

    admin_user = _make_admin_user()

    async def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db

    # Override all require_permission dependencies used by admin routes
    from src.auth.dependencies import get_current_user, require_permission

    # The admin routes use Depends(require_permission("system.admin"))
    # which returns a dependency function. We need to override the inner function.
    # Easiest: override get_current_user to return admin, and patch permission check.
    async def override_get_user():
        return admin_user

    test_app.dependency_overrides[get_current_user] = override_get_user

    return test_app


@pytest_asyncio.fixture
async def seeded_db(db_session: AsyncSession):
    """Seed the DB with roles, admin user, provider type, and permission."""
    admin_role = Role(id=1, name="admin", description="Full access", is_system=True)
    user_role = Role(id=2, name="user", description="Standard user", is_system=True)
    viewer_role = Role(id=3, name="viewer", description="Read only", is_system=True)
    db_session.add_all([admin_role, user_role, viewer_role])
    await db_session.flush()

    admin_user = User(
        id=1,
        username="admin",
        password_hash=hash_password("adminpass1"),
        auth_method="local",
        role_id=1,
        is_active=True,
    )
    db_session.add(admin_user)
    await db_session.flush()

    pt = ProviderType(
        id="mock",
        display_name="Mock Provider",
        icon="mock",
        category="media",
        config_schema=json.dumps({
            "fields": [
                {"key": "url", "label": "URL", "type": "url", "required": True},
                {"key": "api_key", "label": "API Key", "type": "secret", "required": True},
            ]
        }),
        default_intervals=json.dumps({
            "health_seconds": 30,
            "summary_seconds": 60,
            "detail_cache_seconds": 300,
        }),
    )
    db_session.add(pt)
    await db_session.flush()

    perm = Permission(
        id=1,
        key="mock.view",
        display_name="View Mock Data",
        description="View mock data",
        provider_type="mock",
        category="read",
    )
    db_session.add(perm)
    await db_session.flush()

    db_session.add(RolePermission(role_id=1, permission_id=1))
    await db_session.commit()

    return db_session


@pytest_asyncio.fixture
async def client(seeded_db: AsyncSession):
    """Create an async test client with a standalone test app."""
    test_app = _create_test_app(seeded_db)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Provider Management
# ---------------------------------------------------------------------------

class TestProviderList:
    @pytest.mark.asyncio
    async def test_list_providers(self, client: AsyncClient):
        resp = await client.get("/admin/providers", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Provider Instances" in resp.text

    @pytest.mark.asyncio
    async def test_list_shows_add_button(self, client: AsyncClient):
        resp = await client.get("/admin/providers", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Add Provider" in resp.text


class TestProviderNew:
    @pytest.mark.asyncio
    async def test_new_form_no_type(self, client: AsyncClient):
        resp = await client.get("/admin/providers/new", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Add Provider" in resp.text

    @pytest.mark.asyncio
    async def test_new_form_with_type(self, client: AsyncClient):
        resp = await client.get(
            "/admin/providers/new?type_id=mock",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "Configuration" in resp.text


class TestProviderCreate:
    @pytest.mark.asyncio
    async def test_create_missing_fields(self, client: AsyncClient):
        resp = await client.post(
            "/admin/providers",
            data={"type_id": "", "display_name": ""},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "required" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_create_unknown_type(self, client: AsyncClient):
        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.get_provider_class.return_value = None
            resp = await client.post(
                "/admin/providers",
                data={"type_id": "nonexistent", "display_name": "Test"},
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 400
        assert "Unknown" in resp.text

    @pytest.mark.asyncio
    async def test_create_success(self, client: AsyncClient):
        mock_class = MagicMock()
        mock_meta = MagicMock()
        mock_meta.config_schema = {
            "fields": [
                {"key": "url", "type": "url", "required": True},
            ]
        }
        mock_class.meta.return_value = mock_meta

        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.get_provider_class.return_value = mock_class
            mock_reg.add_instance = AsyncMock(return_value=1)
            resp = await client.post(
                "/admin/providers",
                data={
                    "type_id": "mock",
                    "display_name": "My Mock",
                    "config_url": "http://test:8080",
                },
                headers={"HX-Request": "true"},
                follow_redirects=False,
            )
        assert resp.status_code == 200
        assert resp.headers.get("hx-redirect") == "/admin/providers"

    @pytest.mark.asyncio
    async def test_create_add_instance_fails(self, client: AsyncClient):
        mock_class = MagicMock()
        mock_meta = MagicMock()
        mock_meta.config_schema = {"fields": []}
        mock_class.meta.return_value = mock_meta

        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.get_provider_class.return_value = mock_class
            mock_reg.add_instance = AsyncMock(return_value=None)
            resp = await client.post(
                "/admin/providers",
                data={"type_id": "mock", "display_name": "Fail"},
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 400
        assert "Failed" in resp.text


class TestProviderDelete:
    @pytest.mark.asyncio
    async def test_delete_not_found(self, client: AsyncClient):
        resp = await client.delete(
            "/admin/providers/9999",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_success(self, client: AsyncClient, seeded_db: AsyncSession):
        inst = ProviderInstance(
            id=10, provider_type_id="mock", display_name="To Delete",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300, is_enabled=False,
        )
        seeded_db.add(inst)
        await seeded_db.commit()

        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.remove_instance = AsyncMock()
            resp = await client.delete(
                "/admin/providers/10",
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200


class TestProviderTest:
    @pytest.mark.asyncio
    async def test_test_connection_success(self, client: AsyncClient):
        mock_provider = MagicMock()
        mock_provider.validate_config = AsyncMock(return_value=(True, "Connected to Mock v1.0"))

        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.get_instance.return_value = mock_provider
            resp = await client.post(
                "/admin/providers/1/test",
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert "success" in resp.text
        assert "Connected" in resp.text

    @pytest.mark.asyncio
    async def test_test_connection_failure(self, client: AsyncClient):
        mock_provider = MagicMock()
        mock_provider.validate_config = AsyncMock(return_value=(False, "Connection refused"))

        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.get_instance.return_value = mock_provider
            resp = await client.post(
                "/admin/providers/1/test",
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert "error" in resp.text

    @pytest.mark.asyncio
    async def test_test_not_running(self, client: AsyncClient, seeded_db: AsyncSession):
        inst = ProviderInstance(
            id=20, provider_type_id="mock", display_name="Disabled",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300, is_enabled=False,
        )
        seeded_db.add(inst)
        await seeded_db.commit()

        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.get_instance.return_value = None
            resp = await client.post(
                "/admin/providers/20/test",
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert "not running" in resp.text.lower()


class TestProviderToggle:
    @pytest.mark.asyncio
    async def test_toggle_not_found(self, client: AsyncClient):
        resp = await client.post(
            "/admin/providers/9999/toggle",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_toggle_disable(self, client: AsyncClient, seeded_db: AsyncSession):
        inst = ProviderInstance(
            id=30, provider_type_id="mock", display_name="Running",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300, is_enabled=True,
        )
        seeded_db.add(inst)
        await seeded_db.commit()

        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.remove_instance = AsyncMock()
            resp = await client.post(
                "/admin/providers/30/toggle",
                headers={"HX-Request": "true"},
                follow_redirects=False,
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

class TestUserList:
    @pytest.mark.asyncio
    async def test_list_users(self, client: AsyncClient):
        resp = await client.get("/admin/users", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Users" in resp.text


class TestUserCreate:
    @pytest.mark.asyncio
    async def test_create_missing_fields(self, client: AsyncClient):
        resp = await client.post(
            "/admin/users",
            data={"username": "", "password": ""},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_short_password(self, client: AsyncClient):
        resp = await client.post(
            "/admin/users",
            data={"username": "testuser", "password": "short"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "8 characters" in resp.text

    @pytest.mark.asyncio
    async def test_create_success(self, client: AsyncClient):
        resp = await client.post(
            "/admin/users",
            data={
                "username": "newuser",
                "password": "longpassword123",
                "role_id": "2",
            },
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert resp.headers.get("hx-redirect") == "/admin/users"

    @pytest.mark.asyncio
    async def test_create_duplicate(self, client: AsyncClient):
        resp = await client.post(
            "/admin/users",
            data={"username": "admin", "password": "longpassword123", "role_id": "1"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.text


class TestUserEdit:
    @pytest.mark.asyncio
    async def test_edit_form(self, client: AsyncClient):
        resp = await client.get("/admin/users/1/edit", headers={"HX-Request": "true"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_edit_not_found(self, client: AsyncClient):
        resp = await client.get("/admin/users/9999/edit", headers={"HX-Request": "true"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_role(self, client: AsyncClient, seeded_db: AsyncSession):
        user2 = User(
            id=2, username="testuser",
            password_hash=hash_password("testpassword1"),
            auth_method="local", role_id=2, is_active=True,
        )
        seeded_db.add(user2)
        await seeded_db.commit()

        resp = await client.put(
            "/admin/users/2",
            data={"role_id": "3"},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_not_found(self, client: AsyncClient):
        resp = await client.put(
            "/admin/users/9999",
            data={"role_id": "1"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_short_password(self, client: AsyncClient):
        resp = await client.put(
            "/admin/users/1",
            data={"password": "short"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "8 characters" in resp.text


class TestUserDelete:
    @pytest.mark.asyncio
    async def test_delete_self_denied(self, client: AsyncClient):
        resp = await client.delete(
            "/admin/users/1",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "own account" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_delete_not_found(self, client: AsyncClient):
        resp = await client.delete(
            "/admin/users/9999",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_success(self, client: AsyncClient, seeded_db: AsyncSession):
        user2 = User(
            id=3, username="todelete",
            password_hash=hash_password("testpassword1"),
            auth_method="local", role_id=2, is_active=True,
        )
        seeded_db.add(user2)
        await seeded_db.commit()

        resp = await client.delete(
            "/admin/users/3",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200


class TestForceReset:
    @pytest.mark.asyncio
    async def test_force_reset_not_found(self, client: AsyncClient):
        resp = await client.post(
            "/admin/users/9999/force-reset",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_force_reset_toggle(self, client: AsyncClient, seeded_db: AsyncSession):
        user2 = User(
            id=4, username="resetme",
            password_hash=hash_password("testpassword1"),
            auth_method="local", role_id=2, is_active=True, force_reset=False,
        )
        seeded_db.add(user2)
        await seeded_db.commit()

        resp = await client.post(
            "/admin/users/4/force-reset",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Role Management
# ---------------------------------------------------------------------------

class TestRoleList:
    @pytest.mark.asyncio
    async def test_list_roles(self, client: AsyncClient):
        resp = await client.get("/admin/roles", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Roles" in resp.text


class TestRoleCreate:
    @pytest.mark.asyncio
    async def test_create_missing_name(self, client: AsyncClient):
        resp = await client.post(
            "/admin/roles",
            data={"name": "", "description": ""},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_duplicate(self, client: AsyncClient):
        resp = await client.post(
            "/admin/roles",
            data={"name": "admin", "description": "duplicate"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.text

    @pytest.mark.asyncio
    async def test_create_success(self, client: AsyncClient):
        resp = await client.post(
            "/admin/roles",
            data={"name": "custom", "description": "Custom role"},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert resp.headers.get("hx-redirect") == "/admin/roles"


class TestRoleEdit:
    @pytest.mark.asyncio
    async def test_edit_form(self, client: AsyncClient):
        resp = await client.get("/admin/roles/1/edit", headers={"HX-Request": "true"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_edit_not_found(self, client: AsyncClient):
        resp = await client.get("/admin/roles/9999/edit", headers={"HX-Request": "true"})
        assert resp.status_code == 404


class TestRoleUpdate:
    @pytest.mark.asyncio
    async def test_update_permissions(self, client: AsyncClient):
        resp = await client.put(
            "/admin/roles/1",
            data={"permission_ids": ["1"]},
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert resp.headers.get("hx-redirect") == "/admin/roles"

    @pytest.mark.asyncio
    async def test_update_not_found(self, client: AsyncClient):
        resp = await client.put(
            "/admin/roles/9999",
            data={},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404


class TestRoleDelete:
    @pytest.mark.asyncio
    async def test_delete_system_role(self, client: AsyncClient):
        resp = await client.delete(
            "/admin/roles/1",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "System roles" in resp.text

    @pytest.mark.asyncio
    async def test_delete_not_found(self, client: AsyncClient):
        resp = await client.delete(
            "/admin/roles/9999",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_role_with_users(self, client: AsyncClient, seeded_db: AsyncSession):
        custom = Role(id=10, name="has-users", description="test", is_system=False)
        seeded_db.add(custom)
        await seeded_db.flush()

        user2 = User(
            id=5, username="roleuser",
            password_hash=hash_password("testpassword1"),
            auth_method="local", role_id=10, is_active=True,
        )
        seeded_db.add(user2)
        await seeded_db.commit()

        resp = await client.delete(
            "/admin/roles/10",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "users have this role" in resp.text

    @pytest.mark.asyncio
    async def test_delete_custom_role(self, client: AsyncClient, seeded_db: AsyncSession):
        custom = Role(id=11, name="deleteme", description="test", is_system=False)
        seeded_db.add(custom)
        await seeded_db.commit()

        resp = await client.delete(
            "/admin/roles/11",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    @pytest.mark.asyncio
    async def test_settings_page(self, client: AsyncClient):
        with patch("src.routes.admin.registry") as mock_reg:
            mock_reg.get_registered_types.return_value = {"mock": MagicMock}
            resp = await client.get("/admin/settings", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "System Settings" in resp.text


# ---------------------------------------------------------------------------
# Sort Order
# ---------------------------------------------------------------------------

class TestSortOrder:
    @pytest.mark.asyncio
    async def test_sort_providers(self, client: AsyncClient, seeded_db: AsyncSession):
        inst1 = ProviderInstance(
            id=40, provider_type_id="mock", display_name="A",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300, sort_order=0,
        )
        inst2 = ProviderInstance(
            id=41, provider_type_id="mock", display_name="B",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300, sort_order=1,
        )
        seeded_db.add_all([inst1, inst2])
        await seeded_db.commit()

        resp = await client.post(
            "/admin/providers/sort",
            data={"order[]": ["41", "40"]},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
