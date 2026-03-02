"""Docker provider — connects to Docker Engine API via Unix socket for container monitoring."""

import asyncio
import os
import socket
from datetime import datetime
from typing import Any

import httpx
import structlog

from src.providers.base import (
    ActionDefinition,
    ActionResult,
    BaseProvider,
    DetailResult,
    HealthResult,
    HealthStatus,
    PermissionDef,
    ProviderMeta,
    SummaryResult,
)
from src.utils.formatting import format_bytes

logger = structlog.get_logger()

# Container state → display mapping
STATE_DISPLAY = {
    "running": "Running",
    "created": "Created",
    "restarting": "Restarting",
    "paused": "Paused",
    "exited": "Stopped",
    "dead": "Dead",
}


class DockerProvider(BaseProvider):
    """Docker Engine provider via Unix socket."""

    _self_container_id: str | None = None

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="docker",
            display_name="Docker",
            icon="docker",
            category="infrastructure",
            config_schema={
                "fields": [
                    {
                        "key": "socket_path",
                        "label": "Docker Socket Path",
                        "type": "string",
                        "required": True,
                        "default": "/var/run/docker.sock",
                        "help_text": "Path to Docker Unix socket",
                    },
                    {
                        "key": "show_all",
                        "label": "Show Stopped Containers",
                        "type": "boolean",
                        "required": False,
                        "default": True,
                        "help_text": "Include stopped/exited containers in the list",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 30,
                "detail_cache_seconds": 60,
            },
            permissions=[
                PermissionDef("docker.view", "View Docker Data", "Container list, stats", "read"),
                PermissionDef("docker.restart", "Restart Containers", "Restart a running container", "action"),
                PermissionDef("docker.startstop", "Start/Stop Containers", "Start or stop containers", "admin"),
            ],
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create an httpx client connected to the Docker socket."""
        # The provider framework sets self.http_client but for Docker
        # we need a Unix socket transport. If the current client isn't
        # socket-based, we return it anyway (for tests with mock transport).
        return self.http_client

    def _detect_self_container_id(self) -> str | None:
        """Detect our own container ID to prevent self-actions."""
        if self._self_container_id is not None:
            return self._self_container_id

        # Try hostname (Docker sets hostname to container ID by default)
        hostname = socket.gethostname()
        if len(hostname) == 12 and all(c in "0123456789abcdef" for c in hostname):
            self._self_container_id = hostname
            return hostname

        # Try /proc/self/cgroup (Linux containers)
        try:
            cgroup = open("/proc/self/cgroup").read()
            for line in cgroup.splitlines():
                parts = line.split("/")
                if len(parts) > 2 and len(parts[-1]) >= 12:
                    cid = parts[-1][:12]
                    if all(c in "0123456789abcdef" for c in cid):
                        self._self_container_id = cid
                        return cid
        except (FileNotFoundError, PermissionError):
            pass

        self._self_container_id = ""
        return ""

    def _is_self(self, container_id: str) -> bool:
        """Check if a container ID is our own."""
        self_id = self._detect_self_container_id()
        if not self_id:
            return False
        return container_id[:12] == self_id[:12]

    @staticmethod
    def _normalize_container(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize container data from /containers/json, stripping sensitive fields per H2."""
        name = ""
        names = raw.get("Names", [])
        if names:
            name = names[0].lstrip("/")

        # Extract ports
        ports: list[dict] = []
        for p in raw.get("Ports", []):
            ports.append({
                "private": p.get("PrivatePort"),
                "public": p.get("PublicPort"),
                "type": p.get("Type", "tcp"),
            })

        state = raw.get("State", "unknown")
        health = ""
        status_str = raw.get("Status", "")
        if "(healthy)" in status_str:
            health = "healthy"
        elif "(unhealthy)" in status_str:
            health = "unhealthy"
        elif "(starting)" in status_str or "(health: starting)" in status_str:
            health = "starting"

        return {
            "id": raw.get("Id", "")[:12],
            "name": name,
            "image": raw.get("Image", "unknown"),
            "state": state,
            "state_display": STATE_DISPLAY.get(state, state),
            "status": status_str,
            "health": health,
            "created": raw.get("Created", 0),
            "ports": ports,
            "mounts": len(raw.get("Mounts", [])),
            # Env, volume host paths, and network details intentionally omitted (H2)
        }

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        try:
            client = self._get_client()
            start = datetime.utcnow()
            resp = await client.get("/version")
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if resp.status_code != 200:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message=f"Unexpected status: {resp.status_code}",
                    response_time_ms=elapsed,
                )

            try:
                data = resp.json()
            except Exception:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Not a Docker instance (unexpected response)",
                    response_time_ms=elapsed,
                )
            if "Version" not in data or "ApiVersion" not in data:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Not a Docker instance (missing version fields)",
                    response_time_ms=elapsed,
                )
            version = data["Version"]

            return HealthResult(
                status=HealthStatus.UP,
                message=f"Docker {version}",
                response_time_ms=elapsed,
                details={"version": version},
            )

        except FileNotFoundError:
            return HealthResult(status=HealthStatus.DOWN, message="Docker socket not mounted")
        except PermissionError:
            return HealthResult(status=HealthStatus.DOWN, message="Socket permission denied")
        except httpx.TimeoutException:
            return HealthResult(status=HealthStatus.DOWN, message="Docker daemon not responding")
        except httpx.ConnectError:
            return HealthResult(status=HealthStatus.DOWN, message="Docker socket not available")
        except Exception as e:
            return HealthResult(status=HealthStatus.DOWN, message=f"Error: {str(e)}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_summary(self) -> SummaryResult:
        empty = self._empty_summary()
        try:
            client = self._get_client()
            show_all = self.config.get("show_all", True)

            results = await asyncio.gather(
                client.get("/containers/json", params={"all": str(show_all).lower()}),
                client.get("/info"),
                return_exceptions=True,
            )

            containers_resp, info_resp = results

            # Parse containers
            container_list: list[dict] = []
            running = 0
            stopped = 0
            unhealthy = 0
            if not isinstance(containers_resp, Exception) and containers_resp.status_code == 200:
                for c in containers_resp.json():
                    normalized = self._normalize_container(c)
                    container_list.append(normalized)
                    state = normalized["state"]
                    if state == "running":
                        running += 1
                    elif state in ("exited", "dead", "created"):
                        stopped += 1
                    if normalized["health"] == "unhealthy":
                        unhealthy += 1

            # Parse system info
            system: dict = {}
            docker_version = "unknown"
            if not isinstance(info_resp, Exception) and info_resp.status_code == 200:
                info = info_resp.json()
                docker_version = info.get("ServerVersion", "unknown")
                mem_total = info.get("MemTotal", 0)
                system = {
                    "images": info.get("Images", 0),
                    "memory_total": mem_total,
                    "memory_total_formatted": format_bytes(mem_total),
                    "cpus": info.get("NCPU", 0),
                    "os": info.get("OperatingSystem", "unknown"),
                    "kernel": info.get("KernelVersion", "unknown"),
                }

            data = {
                "docker_version": docker_version,
                "containers": {
                    "total": len(container_list),
                    "running": running,
                    "stopped": stopped,
                    "unhealthy": unhealthy,
                },
                "container_list": container_list,
                "system": system,
            }

            return SummaryResult(data=data, fetched_at=datetime.utcnow())

        except Exception as e:
            logger.warning("docker_summary_failed", instance_id=self.instance_id, error=str(e))
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "docker_version": None,
                "containers": {"total": 0, "running": 0, "stopped": 0, "unhealthy": 0},
                "container_list": [],
                "system": {},
            },
            fetched_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    async def get_detail(self) -> DetailResult:
        empty = DetailResult(
            data={"containers": [], "system": {}, "version": {}},
            fetched_at=datetime.utcnow(),
        )
        try:
            client = self._get_client()

            results = await asyncio.gather(
                client.get("/containers/json", params={"all": "true"}),
                client.get("/info"),
                client.get("/version"),
                return_exceptions=True,
            )

            containers_resp, info_resp, version_resp = results

            # Containers
            containers: list[dict] = []
            if not isinstance(containers_resp, Exception) and containers_resp.status_code == 200:
                for c in containers_resp.json():
                    containers.append(self._normalize_container(c))

            # System info (stripped of sensitive data per H2)
            system: dict = {}
            if not isinstance(info_resp, Exception) and info_resp.status_code == 200:
                info = info_resp.json()
                mem_total = info.get("MemTotal", 0)
                system = {
                    "images": info.get("Images", 0),
                    "memory_total": mem_total,
                    "memory_total_formatted": format_bytes(mem_total),
                    "cpus": info.get("NCPU", 0),
                    "os": info.get("OperatingSystem", "unknown"),
                    "kernel": info.get("KernelVersion", "unknown"),
                    "storage_driver": info.get("Driver", "unknown"),
                    "containers_total": info.get("Containers", 0),
                    "containers_running": info.get("ContainersRunning", 0),
                    "containers_paused": info.get("ContainersPaused", 0),
                    "containers_stopped": info.get("ContainersStopped", 0),
                }

            # Version info
            version: dict = {}
            if not isinstance(version_resp, Exception) and version_resp.status_code == 200:
                v = version_resp.json()
                version = {
                    "version": v.get("Version", "unknown"),
                    "api_version": v.get("ApiVersion", "unknown"),
                    "os": v.get("Os", "unknown"),
                    "arch": v.get("Arch", "unknown"),
                    "kernel_version": v.get("KernelVersion", "unknown"),
                    "build_time": v.get("BuildTime", ""),
                }

            return DetailResult(
                data={
                    "containers": containers,
                    "system": system,
                    "version": version,
                },
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.warning("docker_detail_failed", instance_id=self.instance_id, error=str(e))
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="restart_container",
                display_name="Restart Container",
                permission="docker.restart",
                category="action",
                confirm=True,
                confirm_message="Restart this container?",
                params_schema={
                    "properties": {"container_id": {"type": "string", "required": True}}
                },
            ),
            ActionDefinition(
                key="stop_container",
                display_name="Stop Container",
                permission="docker.startstop",
                category="admin",
                confirm=True,
                confirm_message="Stop this container?",
                params_schema={
                    "properties": {"container_id": {"type": "string", "required": True}}
                },
            ),
            ActionDefinition(
                key="start_container",
                display_name="Start Container",
                permission="docker.startstop",
                category="admin",
                params_schema={
                    "properties": {"container_id": {"type": "string", "required": True}}
                },
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        try:
            container_id = params.get("container_id", "")
            if not container_id and action in ("restart_container", "stop_container", "start_container"):
                return ActionResult(success=False, message="No container ID provided")

            # Self-protection: never act on our own container
            if container_id and self._is_self(container_id):
                return ActionResult(success=False, message="Cannot perform actions on the Great Eye container")

            client = self._get_client()

            if action == "restart_container":
                resp = await client.post(f"/containers/{container_id}/restart")
                if resp.status_code == 204:
                    return ActionResult(success=True, message="Container restarted", invalidate_cache=True)
                return ActionResult(success=False, message=f"Restart failed: HTTP {resp.status_code}")

            elif action == "stop_container":
                resp = await client.post(f"/containers/{container_id}/stop")
                if resp.status_code == 204:
                    return ActionResult(success=True, message="Container stopped", invalidate_cache=True)
                if resp.status_code == 304:
                    return ActionResult(success=True, message="Container already stopped")
                return ActionResult(success=False, message=f"Stop failed: HTTP {resp.status_code}")

            elif action == "start_container":
                resp = await client.post(f"/containers/{container_id}/start")
                if resp.status_code == 204:
                    return ActionResult(success=True, message="Container started", invalidate_cache=True)
                if resp.status_code == 304:
                    return ActionResult(success=True, message="Container already running")
                return ActionResult(success=False, message=f"Start failed: HTTP {resp.status_code}")

            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")

        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    # ------------------------------------------------------------------
    # Validate Config
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        try:
            client = self._get_client()
            resp = await client.get("/version")
            if resp.status_code == 200:
                data = resp.json()
                version = data.get("Version", "unknown")
                return True, f"Connected to Docker Engine v{version}"
            return False, f"Unexpected response: HTTP {resp.status_code}"

        except FileNotFoundError:
            socket_path = self.config.get("socket_path", "/var/run/docker.sock")
            return False, f"Docker socket not found at {socket_path}"
        except PermissionError:
            return False, "Permission denied — check socket permissions"
        except httpx.ConnectError:
            return False, "Docker socket not available"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
