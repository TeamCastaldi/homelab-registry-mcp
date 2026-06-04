"""Pure matching/merge helpers used by the reconciler.

Matching precedence: name, then traefik_router, then a shared URL host. Matching
on the host (rather than the exact URL) is what links an Authentik proxy
provider's external host to the Traefik-discovered service for the same host.
Discovery only enriches provenance fields (host, urls, traefik_router,
authentik_app_slug, auth_mode); curated fields (display_name, category, tags,
notes) on an existing service are never overwritten by a discovery pass.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.models import AuthMode, Service, SourceType

# Auth modes that represent an Authentik proxy enforcing authentication.
_PROXY_AUTH_MODES = {AuthMode.forward_auth, AuthMode.oauth2_proxy}


def _hosts(urls: list[str]) -> set[str]:
    hosts: set[str] = set()
    for url in urls:
        parsed = urlparse(url if "//" in url else f"//{url}")
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def match_service(services: list[Service], discovered: DiscoveredService) -> Service | None:
    by_name = next((s for s in services if s.name == discovered.name), None)
    if by_name is not None:
        return by_name
    if discovered.traefik_router:
        by_router = next(
            (s for s in services if s.traefik_router == discovered.traefik_router), None
        )
        if by_router is not None:
            return by_router
    wanted_hosts = _hosts(discovered.urls)
    if wanted_hosts:
        return next((s for s in services if wanted_hosts & _hosts(s.urls)), None)
    return None


def provenance_updates(service: Service, discovered: DiscoveredService) -> dict[str, Any]:
    """Return {field: new_value} for provenance fields that materially changed."""
    updates: dict[str, Any] = {}

    if discovered.traefik_router and discovered.traefik_router != service.traefik_router:
        updates["traefik_router"] = discovered.traefik_router
    slug = discovered.authentik_app_slug
    if slug and slug != service.authentik_app_slug:
        updates["authentik_app_slug"] = slug
    if (
        discovered.auth_mode
        and discovered.auth_mode != AuthMode.unknown
        and discovered.auth_mode != service.auth_mode
    ):
        # Traefik seeing no middleware on a router does not override an auth_mode
        # that Authentik has confirmed is actively enforced. The discrepancy is
        # captured in auth_mode_conflict; auth_mode holds the intended state.
        traefik_demoting = (
            discovered.source == SourceType.traefik
            and discovered.auth_mode == AuthMode.none
            and service.authentik_auth_mode in _PROXY_AUTH_MODES
        )
        if not traefik_demoting:
            updates["auth_mode"] = discovered.auth_mode
    if discovered.host and discovered.host != service.host:
        updates["host"] = discovered.host
    if discovered.urls:
        merged = list(dict.fromkeys([*service.urls, *discovered.urls]))
        if set(merged) != set(service.urls):
            updates["urls"] = merged

    # Record per-source auth_mode
    if discovered.source == SourceType.traefik and discovered.auth_mode != AuthMode.unknown:
        updates["traefik_auth_mode"] = discovered.auth_mode
    elif discovered.source == SourceType.authentik and discovered.auth_mode != AuthMode.unknown:
        updates["authentik_auth_mode"] = discovered.auth_mode

    # Compute conflict using per-source values after this update
    effective_traefik = updates.get("traefik_auth_mode") or service.traefik_auth_mode
    effective_authentik = updates.get("authentik_auth_mode") or service.authentik_auth_mode
    effective_router = updates.get("traefik_router") or service.traefik_router
    effective_slug = updates.get("authentik_app_slug") or service.authentik_app_slug

    if effective_router and effective_slug and effective_traefik and effective_authentik:
        conflict = effective_traefik == AuthMode.none and effective_authentik in _PROXY_AUTH_MODES
        if conflict != service.auth_mode_conflict:
            updates["auth_mode_conflict"] = conflict

    return updates
