"""Discover and link services from Authentik applications and their providers."""

from __future__ import annotations

from typing import Any

from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.integrations.authentik.client import AuthentikClient
from registry_mcp.models import AuthMode, Category, SourceType
import os

_DEFAULT_INFRA_NAMES = {
    "traefik",
    "authentik",
    "gitea",
    "registry",
    "vscode",
}

def _build_infra_names() -> set[str]:
    extras = os.getenv("DISCOVERY_EXCLUDE_NAMES", "")
    extra_set = {n.strip() for n in extras.split(",") if n.strip()}
    return _DEFAULT_INFRA_NAMES | extra_set

_INFRA_NAMES = _build_infra_names()
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
_SECURITY_NAMES = {"vaultwarden"}


def _is_proxy(provider: dict[str, Any]) -> bool:
    marker = f"{provider.get('component', '')} {provider.get('meta_model_name', '')}".lower()
    return "proxy" in marker


def _is_oauth2(provider: dict[str, Any]) -> bool:
    marker = f"{provider.get('component', '')} {provider.get('meta_model_name', '')}".lower()
    return "oauth2" in marker or "openid" in marker


def _infer_category(name: str, group: str) -> Category:
    key = name.lower()
    grp = group.lower() if group else ""
    if key in _MEDIA_NAMES or "media" in grp:
        return Category.media
    if key in _INFRA_NAMES or "infra" in grp:
        return Category.infra
    if key in _SECURITY_NAMES or "security" in grp:
        return Category.security
    if "monitoring" in grp:
        return Category.monitoring
    return Category.app


class AuthentikDiscoverySource:
    """Authentik applications become candidate services, linked by their provider.

    Applications fronted by a proxy provider are flagged `auth_mode=forward_auth`;
    OAuth2/OpenID providers (Authentik as OIDC IdP) are flagged `auth_mode=oauth2_oidc`.
    """

    source = SourceType.authentik

    def __init__(self, client: AuthentikClient) -> None:
        self._client = client

    async def discover(self) -> list[DiscoveredService]:
        applications = await self._client.list_applications()
        providers = {p.get("pk"): p for p in await self._client.list_providers()}

        discovered: list[DiscoveredService] = []
        for app in applications:
            slug = app.get("slug")
            if not slug:
                continue
            provider = providers.get(app.get("provider")) or {}
            urls = []
            auth_mode = AuthMode.unknown
            if _is_proxy(provider):
                auth_mode = AuthMode.forward_auth
                external_host = provider.get("external_host")
                if external_host:
                    urls.append(external_host)
            elif _is_oauth2(provider):
                auth_mode = AuthMode.oauth2_oidc
            launch_url = app.get("launch_url") or app.get("meta_launch_url")
            if launch_url and launch_url not in urls:
                urls.append(launch_url)
            group = app.get("group") or ""
            category = _infer_category(slug, group)
            discovered.append(
                DiscoveredService(
                    source=SourceType.authentik,
                    external_id=slug,
                    name=slug,
                    display_name=app.get("name"),
                    category=category,
                    urls=urls,
                    authentik_app_slug=slug,
                    auth_mode=auth_mode,
                    raw=app,
                )
            )
        return discovered
