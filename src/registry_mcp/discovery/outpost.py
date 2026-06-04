"""Deterministic Authentik outpost-sidecar detection, shared across discovery.

The *arr stack runs each service behind a per-service Authentik outpost sidecar:
the Traefik router (e.g. ``prowlarr-proxy@docker``) points at an outpost
container (e.g. ``prowlarr_outpost``) which performs the authentication and then
proxies to the app. That router carries no Traefik auth middleware because the
outpost *is* the auth layer — so naive middleware inspection would mislabel it
``auth_mode=none`` and raise a false ``auth_mode_conflict``.

These helpers are pure and rule-based (no LLM, no I/O), keeping the perception
layer deterministic. An outpost container is recognised by a name ending in
``_outpost`` / ``-outpost`` or by the official ``ghcr.io/goauthentik/proxy``
image; its service *base* (``prowlarr_outpost`` -> ``prowlarr``) is matched
against a Traefik router's normalised name to decide whether the router is
already forward-auth protected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_OUTPOST_NAME_RE = re.compile(r"[-_]outpost$")
# Strip a trailing outpost/proxy marker to recover the service base name, so the
# outpost container base aligns with the Traefik router's normalised name.
_OUTPOST_SUFFIX_RE = re.compile(r"[-_](outpost|proxy)$")
_OUTPOST_IMAGE_PREFIX = "ghcr.io/goauthentik/proxy"


def is_outpost_container(name: str | None, image: str | None) -> bool:
    """True when a container is an Authentik outpost sidecar."""
    normalized = (name or "").lstrip("/").lower()
    img = (image or "").lower()
    return bool(_OUTPOST_NAME_RE.search(normalized)) or img.startswith(_OUTPOST_IMAGE_PREFIX)


def outpost_base(name: str | None) -> str:
    """Service base for an outpost container: ``prowlarr_outpost`` -> ``prowlarr``."""
    normalized = (name or "").lstrip("/").lower()
    return _OUTPOST_SUFFIX_RE.sub("", normalized)


def outpost_bases_from_containers(containers: Iterable[Mapping[str, Any]]) -> set[str]:
    """Set of service base names that have an Authentik outpost sidecar.

    Each container is a mapping with at least ``name`` (and optionally ``image``).
    """
    bases: set[str] = set()
    for container in containers:
        name = container.get("name")
        if is_outpost_container(name, container.get("image")):
            base = outpost_base(name)
            if base:
                bases.add(base)
    return bases
