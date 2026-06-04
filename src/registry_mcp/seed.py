"""Seed the registry from a YAML file of known services.

Idempotent: existing services (matched by name) are updated, new ones created.

Usage:
    registry-mcp-seed path/to/services.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from registry_mcp.config import get_settings
from registry_mcp.models import Service
from registry_mcp.registry import RegistryStore


def load_services(path: str | Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("services", [])
    if not isinstance(data, list):
        raise ValueError("YAML must be a list of services or a mapping with a 'services' key")
    return data


def seed(store: RegistryStore, services: list[dict[str, Any]]) -> dict[str, int]:
    created = 0
    updated = 0
    for entry in services:
        name = entry.get("name")
        if not name:
            raise ValueError(f"service entry missing required 'name': {entry!r}")
        existing = store.get_service(name)
        if existing is None:
            store.create_service(Service(**entry), actor="manual:seed")
            created += 1
        else:
            store.update_service(existing.id, entry, actor="manual:seed")
            updated += 1
    return {"created": created, "updated": updated}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: registry-mcp-seed <services.yaml>", file=sys.stderr)
        raise SystemExit(2)
    settings = get_settings()
    store = RegistryStore(settings.registry_db_path)
    result = seed(store, load_services(sys.argv[1]))
    print(f"seeded registry: {result['created']} created, {result['updated']} updated")


if __name__ == "__main__":
    main()
