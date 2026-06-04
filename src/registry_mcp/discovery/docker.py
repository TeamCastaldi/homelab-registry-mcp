"""Discover services from Docker containers labelled for Traefik."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.discovery.outpost import outpost_bases_from_containers
from registry_mcp.models import SourceType

_HOST_RE = re.compile(r"Host\(`([^`]+)`\)")


def _hosts_from_labels(labels: dict[str, str]) -> list[str]:
    hosts: list[str] = []
    for key, value in labels.items():
        if key.startswith("traefik.http.routers.") and key.endswith(".rule"):
            hosts.extend(_HOST_RE.findall(value))
    return hosts


class DockerDiscoverySource:
    """Containers with a `traefik.enable=true` label become candidate services."""

    source = SourceType.docker

    def __init__(self, *, client: Any = None, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url

    def _ensure_client(self) -> Any:
        if self._client is None:
            import docker

            self._client = (
                docker.DockerClient(base_url=self._base_url)
                if self._base_url
                else docker.from_env()
            )
        return self._client

    async def discover(self) -> list[DiscoveredService]:
        return await asyncio.to_thread(self._discover_sync)

    def list_outpost_bases(self) -> set[str]:
        """Service bases of running Authentik outpost sidecars (sync, blocking).

        Used by the Traefik source to recognise the outpost auth pattern. Lists
        the same Traefik-enabled containers as discovery — an outpost carries the
        router labels, so it appears here.
        """
        client = self._ensure_client()
        containers = client.containers.list(filters={"label": "traefik.enable=true"})
        descriptors: list[dict[str, Any]] = []
        for container in containers:
            name = (container.name or "").lstrip("/")
            try:
                image = (container.image.tags or [None])[0]
            except (AttributeError, IndexError):
                image = None
            descriptors.append({"name": name, "image": image})
        return outpost_bases_from_containers(descriptors)

    def _discover_sync(self) -> list[DiscoveredService]:
        client = self._ensure_client()
        containers = client.containers.list(filters={"label": "traefik.enable=true"})
        discovered: list[DiscoveredService] = []
        for container in containers:
            labels = dict(container.labels or {})
            name = (container.name or "").lstrip("/")
            if not name:
                continue
            try:
                image = (container.image.tags or [None])[0]
            except (AttributeError, IndexError):
                image = None
            hosts = _hosts_from_labels(labels)
            discovered.append(
                DiscoveredService(
                    source=SourceType.docker,
                    external_id=str(container.id)[:12],
                    name=name,
                    urls=[f"https://{h}" for h in hosts],
                    raw={
                        "id": str(container.id),
                        "name": name,
                        "image": image,
                        "status": getattr(container, "status", None),
                        "labels": labels,
                    },
                )
            )
        return discovered
