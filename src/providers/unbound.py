"""Unbound provider — connects to unbound-control over TLS for DNS resolver statistics."""

import asyncio
import ssl
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

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

logger = structlog.get_logger()


class UnboundProvider(BaseProvider):
    """Unbound DNS resolver provider via TLS control socket."""

    @staticmethod
    def meta() -> ProviderMeta:
        return ProviderMeta(
            type_id="unbound",
            display_name="Unbound",
            icon="unbound",
            category="network",
            config_schema={
                "fields": [
                    {
                        "key": "host",
                        "label": "Unbound Host",
                        "type": "string",
                        "required": True,
                        "placeholder": "10.0.0.1",
                        "help_text": "IP address of the Unbound server",
                    },
                    {
                        "key": "port",
                        "label": "Control Port",
                        "type": "integer",
                        "required": True,
                        "default": 8953,
                        "help_text": "unbound-control port (default 8953)",
                    },
                    {
                        "key": "server_cert",
                        "label": "Server Certificate",
                        "type": "secret",
                        "required": True,
                        "help_text": "Path or PEM content of unbound_server.pem",
                    },
                    {
                        "key": "control_key",
                        "label": "Control Key",
                        "type": "secret",
                        "required": True,
                        "help_text": "Path or PEM content of unbound_control.key",
                    },
                    {
                        "key": "control_cert",
                        "label": "Control Certificate",
                        "type": "secret",
                        "required": True,
                        "help_text": "Path or PEM content of unbound_control.pem",
                    },
                ]
            },
            default_intervals={
                "health_seconds": 60,
                "summary_seconds": 60,
                "detail_cache_seconds": 300,
            },
            permissions=[
                PermissionDef("unbound.view", "View Unbound Data", "Resolver stats, cache info", "read"),
                PermissionDef("unbound.flush", "Flush Cache", "Clear the DNS cache", "action"),
            ],
        )

    # ------------------------------------------------------------------
    # TLS Connection
    # ------------------------------------------------------------------

    def _build_ssl_context(self) -> ssl.SSLContext:
        """Build an SSL context from config certs/keys."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED

        server_cert = self.config.get("server_cert", "")
        control_key = self.config.get("control_key", "")
        control_cert = self.config.get("control_cert", "")

        # Write PEM content to temp files if not file paths
        server_cert_path = self._resolve_cert(server_cert, "server_cert")
        control_key_path = self._resolve_cert(control_key, "control_key")
        control_cert_path = self._resolve_cert(control_cert, "control_cert")

        ctx.load_verify_locations(server_cert_path)
        ctx.load_cert_chain(certfile=control_cert_path, keyfile=control_key_path)

        return ctx

    @staticmethod
    def _resolve_cert(value: str, name: str) -> str:
        """Resolve a cert config value to a file path.

        If the value looks like a file path that exists, return it directly.
        Otherwise treat it as PEM content and write to a temp file.
        """
        if not value:
            raise ValueError(f"Missing required certificate: {name}")
        path = Path(value)
        if path.exists() and path.is_file():
            return str(path)
        # Treat as PEM content — write to temp file
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
        tmp.write(value)
        tmp.close()
        return tmp.name

    async def _send_command(self, command: str) -> str:
        """Send a command to unbound-control over TLS and return the response."""
        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 8953))
        ssl_ctx = self._build_ssl_context()

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_ctx),
            timeout=10,
        )
        try:
            writer.write(f"UBCT1 {command}\n".encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.read(65536), timeout=10)
            return data.decode("utf-8", errors="replace")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _fetch_stats(self) -> dict[str, str]:
        """Fetch stats_noreset and parse key=value pairs."""
        raw = await self._send_command("stats_noreset")
        stats: dict[str, str] = {}
        for line in raw.strip().splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                stats[key.strip()] = val.strip()
        return stats

    def _parse_stats(self, raw: dict[str, str]) -> dict[str, Any]:
        """Parse raw key=value stats into structured data."""
        def _float(key: str) -> float:
            try:
                return float(raw.get(key, "0"))
            except (ValueError, TypeError):
                return 0.0

        def _int(key: str) -> int:
            return int(_float(key))

        total_queries = _int("total.num.queries")
        cache_hits = _int("total.num.cachehits")
        cache_misses = _int("total.num.cachemiss")
        hit_rate = (cache_hits / max(total_queries, 1)) * 100

        return {
            "queries": {
                "total": total_queries,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "cache_hit_rate": round(hit_rate, 1),
                "prefetch": _int("total.num.prefetch"),
                "expired_served": _int("total.num.expired"),
                "recursive_replies": _int("total.num.recursivereplies"),
            },
            "performance": {
                "recursion_time_avg_ms": round(_float("total.recursion.time.avg") * 1000, 1),
                "recursion_time_median_ms": round(_float("total.recursion.time.median") * 1000, 1),
                "request_list_avg": round(_float("total.requestlist.avg"), 1),
                "request_list_max": _int("total.requestlist.max"),
            },
            "cache": {
                "message_count": _int("msg.cache.count"),
                "rrset_count": _int("rrset.cache.count"),
                "infra_count": _int("infra.cache.count"),
            },
            "security": {
                "unwanted_queries": _int("unwanted.queries"),
                "unwanted_replies": _int("unwanted.replies"),
            },
        }

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> HealthResult:
        try:
            start = datetime.utcnow()
            raw = await self._fetch_stats()
            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            if "total.num.queries" not in raw:
                return HealthResult(
                    status=HealthStatus.DOWN,
                    message="Unexpected response format",
                    response_time_ms=elapsed,
                )

            total = int(float(raw.get("total.num.queries", "0")))
            unwanted = int(float(raw.get("unwanted.queries", "0"))) + int(float(raw.get("unwanted.replies", "0")))

            if unwanted > 0:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"High unwanted traffic ({unwanted})",
                    response_time_ms=elapsed,
                    details={"total_queries": total, "unwanted": unwanted},
                )

            return HealthResult(
                status=HealthStatus.UP,
                message=f"Resolving ({total} queries)",
                response_time_ms=elapsed,
                details={"total_queries": total},
            )

        except ConnectionRefusedError:
            return HealthResult(status=HealthStatus.DOWN, message="Control connection refused")
        except asyncio.TimeoutError:
            return HealthResult(status=HealthStatus.DOWN, message="Connection timed out")
        except ssl.SSLError as e:
            return HealthResult(status=HealthStatus.DOWN, message=f"TLS error: {str(e)}")
        except Exception as e:
            return HealthResult(status=HealthStatus.DOWN, message=f"Error: {str(e)}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    async def get_summary(self) -> SummaryResult:
        empty = self._empty_summary()
        try:
            raw = await self._fetch_stats()
            parsed = self._parse_stats(raw)
            return SummaryResult(data=parsed, fetched_at=datetime.utcnow())
        except Exception as e:
            logger.warning("unbound_summary_failed", instance_id=self.instance_id, error=str(e))
            return empty

    def _empty_summary(self) -> SummaryResult:
        return SummaryResult(
            data={
                "queries": {
                    "total": None, "cache_hits": None, "cache_misses": None,
                    "cache_hit_rate": None, "prefetch": None,
                    "expired_served": None, "recursive_replies": None,
                },
                "performance": {
                    "recursion_time_avg_ms": None, "recursion_time_median_ms": None,
                    "request_list_avg": None, "request_list_max": None,
                },
                "cache": {
                    "message_count": None, "rrset_count": None, "infra_count": None,
                },
                "security": {
                    "unwanted_queries": None, "unwanted_replies": None,
                },
            },
            fetched_at=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------

    async def get_detail(self) -> DetailResult:
        empty = DetailResult(
            data={"stats": {}, "raw_stats": {}},
            fetched_at=datetime.utcnow(),
        )
        try:
            raw = await self._fetch_stats()
            parsed = self._parse_stats(raw)
            return DetailResult(
                data={"stats": parsed, "raw_stats": raw},
                fetched_at=datetime.utcnow(),
            )
        except Exception as e:
            logger.warning("unbound_detail_failed", instance_id=self.instance_id, error=str(e))
            return empty

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="flush_cache",
                display_name="Flush Cache",
                permission="unbound.flush",
                category="action",
                confirm=True,
                confirm_message="Flush Unbound cache? All cached DNS records will be cleared.",
            ),
        ]

    async def execute_action(self, action: str, params: dict[str, Any]) -> ActionResult:
        try:
            if action == "flush_cache":
                response = await self._send_command("flush_zone .")
                if "ok" in response.lower() or response.strip() == "":
                    return ActionResult(success=True, message="Cache flushed", invalidate_cache=True)
                return ActionResult(success=False, message=f"Flush failed: {response.strip()}")
            else:
                return ActionResult(success=False, message=f"Unknown action: {action}")
        except Exception as e:
            return ActionResult(success=False, message=f"Action failed: {str(e)}")

    # ------------------------------------------------------------------
    # Validate Config
    # ------------------------------------------------------------------

    async def validate_config(self) -> tuple[bool, str]:
        try:
            raw = await self._fetch_stats()
            if "total.num.queries" in raw:
                queries = raw["total.num.queries"]
                return True, f"Connected ({queries} total queries)"
            return False, "Connected but unexpected response format"
        except ConnectionRefusedError:
            return False, "Control connection refused — is control-enable set to yes?"
        except ssl.SSLError:
            return False, "TLS certificate error — check control keys"
        except asyncio.TimeoutError:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
