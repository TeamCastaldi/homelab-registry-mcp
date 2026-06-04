"""Ad-hoc live tool test runner. Run with: uv run python _tool_test_runner.py"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from datetime import UTC, datetime

from registry_mcp.config import Settings
from registry_mcp.discovery.engine import DiscoveryEngine, build_sources
from registry_mcp.registry import RegistryStore
from registry_mcp.server import build_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

results: list[dict] = []


async def call(server, name: str, args: dict):
    """Call a tool and return (result, elapsed_ms, error)."""
    t0 = time.perf_counter()
    try:
        _raw, result = await server.call_tool(name, args)
        elapsed = int((time.perf_counter() - t0) * 1000)
        return result, elapsed, None
    except Exception as exc:
        elapsed = int((time.perf_counter() - t0) * 1000)
        return None, elapsed, traceback.format_exc()


def log(name: str, args: dict, result, elapsed: int, error: str | None, notes: str = "", skip: bool = False):
    if skip:
        status = "SKIP"
        reason = error
    else:
        status = "PASS" if error is None and (not isinstance(result, dict) or result.get("error") is None) else "FAIL"
        reason = None
        if error:
            reason = error.strip().splitlines()[-1]
        elif isinstance(result, dict) and result.get("error") is not None:
            reason = result["error"]

    entry = {
        "tool": name,
        "args": args,
        "status": status,
        "elapsed_ms": elapsed,
        "reason": reason,
        "notes": notes,
        "result_summary": _summarise(result),
    }
    results.append(entry)
    tag = {"PASS": "✓", "FAIL": "✗", "SKIP": "⏭"}.get(status, "?")
    print(f"  {tag} {name} ({elapsed}ms) — {status}" + (f": {reason}" if reason else ""))


def _summarise(result) -> str:
    if result is None:
        return "None"
    if isinstance(result, list):
        return f"list[{len(result)}]"
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        keys = list(result.keys())[:5]
        if "items" in result:
            return f"items[{len(result['items'])}]"
        return f"dict({', '.join(keys)})"
    return str(result)[:120]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    settings = Settings()

    # Build a real server backed by a temp DB so registry tests don't pollute data
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp()) / "tool_test.db"
    test_settings = Settings(
        registry_db_path=str(tmp),
        traefik_api_url=settings.traefik_api_url,
        authentik_api_url=settings.authentik_api_url,
        authentik_token=settings.authentik_token,
        traefik_timeout_seconds=settings.traefik_timeout_seconds,
        authentik_timeout_seconds=settings.authentik_timeout_seconds,
    )
    server = build_server(test_settings)

    print(f"\n=== homelab-registry-mcp tool test run ===")
    print(f"Traefik: {test_settings.traefik_api_url}")
    print(f"Authentik: {test_settings.authentik_api_url}")
    print()

    # ------------------------------------------------------------------
    # 1. health
    # ------------------------------------------------------------------
    print("--- health ---")
    r, ms, err = await call(server, "health", {})
    log("health", {}, r, ms, err,
        notes="Expects status=ok, service name, version, timestamp")

    # ------------------------------------------------------------------
    # 2. registry_list_services  (empty db)
    # ------------------------------------------------------------------
    print("\n--- registry ---")
    r, ms, err = await call(server, "registry_list_services", {})
    log("registry_list_services", {}, r, ms, err,
        notes="Empty DB — expects empty list")

    # ------------------------------------------------------------------
    # 3. registry_add_service
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "registry_add_service", {
        "name": "test-svc",
        "display_name": "Test Service",
        "category": "app",
        "host": "lxc-01",
        "urls": ["https://test.example.lan"],
        "tags": ["smoke"],
        "notes": "created by tool test runner",
    })
    log("registry_add_service", {"name": "test-svc"}, r, ms, err,
        notes="Adds a service; expects full service dict back")
    svc_id = r.get("id") if isinstance(r, dict) and "error" not in r else None

    # ------------------------------------------------------------------
    # 4. registry_get_service  (by name)
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "registry_get_service", {"id_or_name": "test-svc"})
    log("registry_get_service", {"id_or_name": "test-svc"}, r, ms, err,
        notes="Lookup by name")

    # ------------------------------------------------------------------
    # 5. registry_update_service
    # ------------------------------------------------------------------
    if svc_id:
        r, ms, err = await call(server, "registry_update_service", {
            "id": svc_id,
            "notes": "updated by tool test runner",
            "tags": ["smoke", "updated"],
        })
        log("registry_update_service", {"id": svc_id[:8] + "..."}, r, ms, err,
            notes="Patches notes + tags")
    else:
        log("registry_update_service", {}, None, 0, "add_service failed",
            notes="Skipped: add failed", skip=True)

    # ------------------------------------------------------------------
    # 6. registry_delete_service
    # ------------------------------------------------------------------
    if svc_id:
        r, ms, err = await call(server, "registry_delete_service", {"id": svc_id})
        log("registry_delete_service", {"id": svc_id[:8] + "..."}, r, ms, err,
            notes="Hard delete; expects deleted=True")
    else:
        log("registry_delete_service", {}, None, 0, "add_service failed",
            notes="Skipped: add failed", skip=True)

    # ------------------------------------------------------------------
    # 7. events_list_discoveries
    # ------------------------------------------------------------------
    print("\n--- events ---")
    r, ms, err = await call(server, "events_list_discoveries", {})
    log("events_list_discoveries", {}, r, ms, err,
        notes="Fresh DB — expects empty list")

    # ------------------------------------------------------------------
    # 8. events_list_changes
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "events_list_changes", {})
    log("events_list_changes", {}, r, ms, err,
        notes="Expects ChangeEvents from the add/update/delete above")

    # ------------------------------------------------------------------
    # 9. discovery_status
    # ------------------------------------------------------------------
    print("\n--- discovery ---")
    r, ms, err = await call(server, "discovery_status", {})
    log("discovery_status", {}, r, ms, err,
        notes="No runs yet — expects sources map with empty status")

    # ------------------------------------------------------------------
    # 10. discovery_run_now  (traefik only)
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "discovery_run_now", {"source": "traefik"})
    log("discovery_run_now", {"source": "traefik"}, r, ms, err,
        notes="Runs live Traefik discovery; expects DiscoveryEvent dict")

    # ------------------------------------------------------------------
    # 11. discovery_list_stale
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "discovery_list_stale", {})
    log("discovery_list_stale", {}, r, ms, err,
        notes="After first run — expects empty (nothing missed yet)")

    # ------------------------------------------------------------------
    # 12. traefik_get_overview
    # ------------------------------------------------------------------
    print("\n--- traefik ---")
    r, ms, err = await call(server, "traefik_get_overview", {})
    log("traefik_get_overview", {}, r, ms, err,
        notes="Live Traefik overview; expects routers/services/middlewares counts")

    # ------------------------------------------------------------------
    # 13. traefik_get_entrypoints
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "traefik_get_entrypoints", {})
    log("traefik_get_entrypoints", {}, r, ms, err,
        notes="Expects items list with web/websecure at minimum")

    # ------------------------------------------------------------------
    # 14. traefik_list_routers
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "traefik_list_routers", {"protocol": "http"})
    log("traefik_list_routers", {"protocol": "http"}, r, ms, err,
        notes="HTTP routers; expects non-empty items list")
    # Grab a router name for the next test
    router_name = None
    if isinstance(r, dict) and "items" in r and r["items"]:
        router_name = r["items"][0].get("name")

    # ------------------------------------------------------------------
    # 15. traefik_get_router
    # ------------------------------------------------------------------
    if router_name:
        r, ms, err = await call(server, "traefik_get_router", {
            "name": router_name, "protocol": "http"
        })
        log("traefik_get_router", {"name": router_name}, r, ms, err,
            notes=f"Single router detail for {router_name!r}")
    else:
        log("traefik_get_router", {}, None, 0, "no routers returned",
            notes="Skipped: list_routers returned nothing")

    # ------------------------------------------------------------------
    # 16. traefik_list_services
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "traefik_list_services", {"protocol": "http"})
    log("traefik_list_services", {"protocol": "http"}, r, ms, err,
        notes="HTTP backend services")

    # ------------------------------------------------------------------
    # 17. traefik_list_middlewares
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "traefik_list_middlewares", {"protocol": "http"})
    log("traefik_list_middlewares", {"protocol": "http"}, r, ms, err,
        notes="HTTP middlewares")

    # ------------------------------------------------------------------
    # 18. traefik_list_tls_certificates
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "traefik_list_tls_certificates", {})
    log("traefik_list_tls_certificates", {}, r, ms, err,
        notes="TLS section of rawdata; may be empty if no certs configured")

    # ------------------------------------------------------------------
    # 19. authentik_list_applications
    # ------------------------------------------------------------------
    print("\n--- authentik ---")
    r, ms, err = await call(server, "authentik_list_applications", {})
    log("authentik_list_applications", {}, r, ms, err,
        notes="All Authentik applications")
    app_slug = None
    if isinstance(r, dict) and "items" in r and r["items"]:
        # slug may be empty string on some Authentik installs; fall back to first non-empty value
        for app in r["items"]:
            candidate = app.get("slug") or app.get("name")
            if candidate:
                app_slug = app.get("slug") or candidate
                break

    # ------------------------------------------------------------------
    # 20. authentik_get_application
    # ------------------------------------------------------------------
    if app_slug:
        r, ms, err = await call(server, "authentik_get_application", {"slug": app_slug})
        log("authentik_get_application", {"slug": app_slug}, r, ms, err,
            notes=f"Single application detail for {app_slug!r}")
    else:
        log("authentik_get_application", {}, None, 0, "no applications configured in Authentik",
            notes="Skipped: list_applications returned empty", skip=True)

    # ------------------------------------------------------------------
    # 21. authentik_list_providers
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "authentik_list_providers", {})
    log("authentik_list_providers", {}, r, ms, err,
        notes="All providers (proxy, oauth2, ldap)")

    # ------------------------------------------------------------------
    # 22. authentik_list_outposts
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "authentik_list_outposts", {})
    log("authentik_list_outposts", {}, r, ms, err,
        notes="Authentik outpost instances")
    outpost_name = None
    if isinstance(r, dict) and "items" in r and r["items"]:
        outpost_name = r["items"][0].get("name")

    # ------------------------------------------------------------------
    # 23. authentik_get_outpost_status
    # ------------------------------------------------------------------
    if outpost_name:
        r, ms, err = await call(server, "authentik_get_outpost_status", {"name": outpost_name})
        log("authentik_get_outpost_status", {"name": outpost_name}, r, ms, err,
            notes=f"Status for outpost {outpost_name!r}")
    else:
        log("authentik_get_outpost_status", {}, None, 0, "no outposts returned",
            notes="Skipped: list_outposts returned nothing or error", skip=True)

    # ------------------------------------------------------------------
    # 24. authentik_list_policies
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "authentik_list_policies", {})
    log("authentik_list_policies", {}, r, ms, err,
        notes="All policies")

    # ------------------------------------------------------------------
    # 25. authentik_search_events
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "authentik_search_events", {
        "within_hours": 24, "limit": 10
    })
    log("authentik_search_events", {"within_hours": 24, "limit": 10}, r, ms, err,
        notes="Last 24h events, max 10")

    # ------------------------------------------------------------------
    # 26. authentik_list_users
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "authentik_list_users", {})
    log("authentik_list_users", {}, r, ms, err,
        notes="All users")

    # ------------------------------------------------------------------
    # 27. authentik_list_groups
    # ------------------------------------------------------------------
    r, ms, err = await call(server, "authentik_list_groups", {})
    log("authentik_list_groups", {}, r, ms, err,
        notes="All groups")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    passed = sum(1 for e in results if e["status"] == "PASS")
    failed = sum(1 for e in results if e["status"] == "FAIL")
    skipped = sum(1 for e in results if e["status"] == "SKIP")
    print(f"\n=== {passed} passed / {failed} failed / {skipped} skipped / {len(results)} total ===\n")

    return results


if __name__ == "__main__":
    data = asyncio.run(main())

    # Write JSON for the markdown log writer
    with open("/tmp/tool_test_results.json", "w") as f:
        json.dump(data, f, indent=2, default=str)
    print("Results written to /tmp/tool_test_results.json")
