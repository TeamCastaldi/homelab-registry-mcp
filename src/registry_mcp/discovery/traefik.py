"""Discover services from Traefik routers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.integrations.traefik.client import TraefikClient
from registry_mcp.logging import get_logger
from registry_mcp.models import AuthMode, Category, SourceType

_log = get_logger("discovery.traefik")

_HOST_RE = re.compile(r"Host\(`([^`]+)`\)")

_INTERNAL_ROUTERS = {
    "api@internal",
    "ping@internal",
    "dashboard@internal",
    "web-to-websecure@internal",
}

_MEDIA_NAMES = {
    "plex",
    "overseerr",
    "sonarr",
    "radarr",
    "prowlarr",
    "sabnzbd",
    "qbittorrent",
    "tunarr",
    "tracearr",
}
_DEFAULT_INFRA_NAMES = {
    "traefik",
    "authentik",
    "gitea",
    "registry",
    "vscode",
}


def _build_infra_names() -> set[str]:
    import os

    extras = os.getenv("DISCOVERY_EXCLUDE_NAMES", "")
    extra_set = {n.strip() for n in extras.split(",") if n.strip()}
    return _DEFAULT_INFRA_NAMES | extra_set


_INFRA_NAMES = _build_infra_names()
_SECURITY_NAMES = {"vaultwarden"}

# Strip a trailing router/proxy marker with either separator so normalised
# router names line up with outpost bases (outpost_base strips ``[-_]`` too);
# e.g. both ``prowlarr-proxy`` and ``prowlarr_proxy`` reduce to ``prowlarr``.
_STRIP_SUFFIXES_RE = re.compile(r"[-_](router|proxy)$")
_STRIP_PROVIDER_RE = re.compile(r"@\w+$")


def _hosts_from_rule(rule: str | None) -> list[str]:
    return _HOST_RE.findall(rule or "")


def _normalize_name(router_name: str) -> str:
    name = _STRIP_PROVIDER_RE.sub("", router_name)
    name = _STRIP_SUFFIXES_RE.sub("", name)
    return name.lower()


def _infer_category(name: str) -> Category:
    if name in _MEDIA_NAMES:
        return Category.media
    if name in _INFRA_NAMES:
        return Category.infra
    if name in _SECURITY_NAMES:
        return Category.security
    return Category.app


class TraefikDiscoverySource:
    """Every HTTP router becomes a candidate service.

    Internal routers are skipped. A router that uses a ForwardAuth middleware is
    flagged `auth_mode=forward_auth`; all others default to `auth_mode=none`.

    Authentik outpost sidecars carry no Traefik auth middleware (the outpost
    *is* the auth layer), so a router whose service base matches a running
    outpost container is also flagged `auth_mode=forward_auth` to avoid a false
    `auth_mode_conflict`. The set of outpost service bases is supplied by an
    optional, injected ``outpost_resolver`` (built from Docker by the engine);
    when absent or failing, detection degrades to the middleware-only behaviour.
    """

    source = SourceType.traefik

    def __init__(
        self,
        client: TraefikClient,
        *,
        outpost_resolver: Callable[[], set[str]] | None = None,
    ) -> None:
        self._client = client
        self._outpost_resolver = outpost_resolver

    async def _outpost_bases(self) -> set[str]:
        """Best-effort set of service bases backed by an outpost; never raises.

        The resolver does blocking Docker I/O, so it runs in a thread. Any
        failure falls back to an empty set — discovery must not fail here.
        """
        if self._outpost_resolver is None:
            return set()
        try:
            return await asyncio.to_thread(self._outpost_resolver)
        except Exception as exc:  # graceful: never fail discovery on outpost lookup
            _log.warning("outpost_resolution_failed", error=str(exc))
            return set()

    async def discover(self) -> list[DiscoveredService]:
        routers = await self._client.list_routers("http")
        middlewares = await self._client.list_middlewares("http")
        forward_auth = {
            m["name"] for m in middlewares if str(m.get("type", "")).lower() == "forwardauth"
        }
        basic_auth = {
            m["name"] for m in middlewares if str(m.get("type", "")).lower() == "basicauth"
        }
        outpost_bases = await self._outpost_bases()

        discovered: list[DiscoveredService] = []
        for router in routers:
            name = router.get("name")
            if not name or name in _INTERNAL_ROUTERS:
                continue
            used = set(router.get("middlewares") or [])
            normalized = _normalize_name(name)
            if used & forward_auth:
                auth_mode = AuthMode.forward_auth
            elif used & basic_auth:
                auth_mode = AuthMode.basic
            elif normalized in outpost_bases:
                # No recognised auth middleware, but the router's service is
                # backed by an Authentik outpost sidecar — the outpost performs
                # the forward auth, so this router is protected, not exposed.
                auth_mode = AuthMode.forward_auth
            else:
                auth_mode = AuthMode.none
            hosts = _hosts_from_rule(router.get("rule"))
            category = _infer_category(normalized)
            discovered.append(
                DiscoveredService(
                    source=SourceType.traefik,
                    external_id=name,
                    name=normalized,
                    display_name=normalized.replace("-", " ").title(),
                    category=category,
                    urls=[f"https://{h}" for h in hosts],
                    traefik_router=name,
                    auth_mode=auth_mode,
                    raw=router,
                )
            )
        return discovered
