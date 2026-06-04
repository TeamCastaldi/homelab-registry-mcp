"""Tests for the Phase 7 reasoning hooks in reconcile and the discovery engine.

These use plain Python stand-in callables / a fake reasoner — no dspy, no LLM —
to verify the injection wiring keeps the deterministic layers LLM-free while
still letting the reasoning layer resolve fuzzy matches and enrich new records.
"""

from registry_mcp.discovery.base import DiscoveredService
from registry_mcp.discovery.engine import DiscoveryEngine
from registry_mcp.models import AuthMode, Category, SourceType


def _traefik_item(name, **kw):
    return DiscoveredService(source=SourceType.traefik, external_id=name, name=name, **kw)


class _Source:
    def __init__(self, source, items):
        self.source = source
        self.items = items

    async def discover(self):
        return list(self.items)


class _FakeReasoner:
    """Stand-in for the DSPy reasoner — no LLM, deterministic outputs."""

    enabled = True

    def __init__(self, *, match_name=None, metadata=None):
        self._match_name = match_name
        self._metadata = metadata

    def resolve_identity(self, candidate, existing):
        if self._match_name and any(e["name"] == self._match_name for e in existing):
            return self._match_name
        return None

    def infer_metadata(self, *, router_rule, middlewares, service_name):
        return self._metadata


# --- store.reconcile: identity_resolver -----------------------------------


def test_identity_resolver_merges_instead_of_creating(store):
    # Seed an existing service the deterministic matcher will later miss.
    store.reconcile(
        SourceType.traefik,
        [_traefik_item("vault", traefik_router="vault@docker", urls=["https://vault.lan"])],
        stale_threshold=3,
    )
    assert len(store.list_services()) == 1

    # Authentik candidate with a different name AND host: deterministic match fails.
    candidate = DiscoveredService(
        source=SourceType.authentik,
        external_id="vaultwarden",
        name="vaultwarden",
        authentik_app_slug="vaultwarden",
        urls=["https://passwords.lan"],
        auth_mode=AuthMode.forward_auth,
    )

    def resolver(item, services):
        return next((s for s in services if s.name == "vault"), None)

    store.reconcile(
        SourceType.authentik, [candidate], stale_threshold=3, identity_resolver=resolver
    )

    services = store.list_services()
    assert len(services) == 1  # merged, not duplicated
    assert services[0].authentik_app_slug == "vaultwarden"
    assert services[0].auth_mode == AuthMode.forward_auth


def test_identity_resolver_returning_none_creates_new(store):
    store.reconcile(
        SourceType.traefik,
        [_traefik_item("vault", urls=["https://vault.lan"])],
        stale_threshold=3,
    )
    candidate = DiscoveredService(
        source=SourceType.authentik,
        external_id="grafana",
        name="grafana",
        urls=["https://grafana.lan"],
    )
    store.reconcile(
        SourceType.authentik,
        [candidate],
        stale_threshold=3,
        identity_resolver=lambda item, services: None,
    )
    assert {s.name for s in store.list_services()} == {"vault", "grafana"}


# --- store.reconcile: metadata_enricher -----------------------------------


def test_metadata_enricher_fills_curated_fields_on_new_service(store):
    item = _traefik_item(
        "grafana",
        traefik_router="grafana@docker",
        urls=["https://grafana.lan"],
        raw={"rule": "Host(`grafana.lan`)", "middlewares": [], "service": "grafana@docker"},
    )

    def enricher(it):
        return {
            "category": Category.monitoring,
            "display_name": "Grafana",
            "notes": "Metrics dashboards",
        }

    store.reconcile(SourceType.traefik, [item], stale_threshold=3, metadata_enricher=enricher)

    svc = store.list_services()[0]
    assert svc.category == Category.monitoring
    assert svc.display_name == "Grafana"
    assert svc.notes == "Metrics dashboards"


def test_metadata_enricher_none_keeps_deterministic_defaults(store):
    item = _traefik_item("grafana", category=Category.app, display_name="grafana")
    store.reconcile(
        SourceType.traefik,
        [item],
        stale_threshold=3,
        metadata_enricher=lambda it: None,
    )
    svc = store.list_services()[0]
    assert svc.category == Category.app
    assert svc.display_name == "grafana"


# --- engine wiring --------------------------------------------------------


async def test_engine_applies_metadata_enrichment(store):
    item = _traefik_item(
        "plex",
        traefik_router="plex@docker",
        urls=["https://plex.lan"],
        raw={"rule": "Host(`plex.lan`)", "middlewares": [], "service": "plex@docker"},
    )
    engine = DiscoveryEngine(
        store,
        {SourceType.traefik: _Source(SourceType.traefik, [item])},
        reasoner=_FakeReasoner(metadata={"category": Category.media, "display_name": "Plex"}),
    )
    await engine.run_source(SourceType.traefik)

    svc = store.list_services()[0]
    assert svc.category == Category.media
    assert svc.display_name == "Plex"


async def test_engine_without_reasoner_is_deterministic(store):
    item = _traefik_item("plex", category=Category.app, display_name="plex")
    engine = DiscoveryEngine(
        store,
        {SourceType.traefik: _Source(SourceType.traefik, [item])},
        reasoner=None,
    )
    await engine.run_source(SourceType.traefik)
    svc = store.list_services()[0]
    assert svc.category == Category.app


async def test_engine_disabled_reasoner_skips_enrichment(store):
    class _Disabled(_FakeReasoner):
        enabled = False

    item = _traefik_item("plex", category=Category.app, display_name="plex")
    engine = DiscoveryEngine(
        store,
        {SourceType.traefik: _Source(SourceType.traefik, [item])},
        reasoner=_Disabled(metadata={"category": Category.media}),
    )
    await engine.run_source(SourceType.traefik)
    svc = store.list_services()[0]
    assert svc.category == Category.app  # disabled reasoner is never consulted


# --- auth_mode race: Traefik must not demote Authentik's proxy auth mode -----


def test_traefik_discovery_does_not_demote_authentik_proxy_auth_mode(store):
    """Authentik reports oauth2_proxy; subsequent Traefik pass (no middleware)
    must not overwrite auth_mode back to none.  auth_mode_conflict should fire
    to capture the discrepancy without losing the intended mode."""
    # Step 1: Authentik pass sets auth_mode = oauth2_proxy
    authentik_item = DiscoveredService(
        source=SourceType.authentik,
        external_id="karakeep",
        name="karakeep",
        authentik_app_slug="kara-keep",
        urls=["https://keep.lan"],
        auth_mode=AuthMode.oauth2_proxy,
    )
    store.reconcile(SourceType.authentik, [authentik_item], stale_threshold=3)
    svc = store.get_service("karakeep")
    assert svc is not None
    assert svc.auth_mode == AuthMode.oauth2_proxy
    assert svc.authentik_auth_mode == AuthMode.oauth2_proxy

    # Step 2: Traefik pass — router has no middleware, so auth_mode=none
    traefik_item = DiscoveredService(
        source=SourceType.traefik,
        external_id="karakeep",
        name="karakeep",
        traefik_router="karakeep@docker",
        urls=["https://keep.lan"],
        auth_mode=AuthMode.none,
    )
    store.reconcile(SourceType.traefik, [traefik_item], stale_threshold=3)
    svc = store.get_service("karakeep")
    assert svc is not None

    # auth_mode must NOT be demoted to none
    assert svc.auth_mode == AuthMode.oauth2_proxy, (
        f"Traefik demoted auth_mode to {svc.auth_mode!r}; expected oauth2_proxy"
    )
    # Per-source fields still reflect reality
    assert svc.traefik_auth_mode == AuthMode.none
    assert svc.authentik_auth_mode == AuthMode.oauth2_proxy
    # The conflict flag must fire because the router is unprotected
    assert svc.auth_mode_conflict is True


def test_oauth2_oidc_provider_does_not_trigger_auth_mode_conflict(store):
    """Authentik acts as OIDC IdP (oauth2_oidc); Traefik has no middleware.
    This is the correct Group-A configuration — no conflict should fire."""
    # Step 1: Authentik pass — OAuth2/OpenID provider, Authentik is the IdP
    authentik_item = DiscoveredService(
        source=SourceType.authentik,
        external_id="gitea",
        name="gitea",
        authentik_app_slug="gitea",
        urls=["https://gitea.lan"],
        auth_mode=AuthMode.oauth2_oidc,
    )
    store.reconcile(SourceType.authentik, [authentik_item], stale_threshold=3)
    svc = store.get_service("gitea")
    assert svc is not None
    assert svc.authentik_auth_mode == AuthMode.oauth2_oidc

    # Step 2: Traefik pass — no forwardAuth middleware (correct for OIDC-native apps)
    traefik_item = DiscoveredService(
        source=SourceType.traefik,
        external_id="gitea",
        name="gitea",
        traefik_router="gitea@docker",
        urls=["https://gitea.lan"],
        auth_mode=AuthMode.none,
    )
    store.reconcile(SourceType.traefik, [traefik_item], stale_threshold=3)
    svc = store.get_service("gitea")
    assert svc is not None

    # oauth2_oidc is not a proxy mode — no conflict expected
    assert svc.auth_mode_conflict is False
    assert svc.traefik_auth_mode == AuthMode.none
    assert svc.authentik_auth_mode == AuthMode.oauth2_oidc
    # Traefik's view becomes the merged auth_mode (no demotion protection needed)
    assert svc.auth_mode == AuthMode.none


def test_traefik_discovery_can_promote_auth_mode_when_no_authentik(store):
    """When there is no Authentik claim, Traefik is free to set auth_mode."""
    traefik_item = DiscoveredService(
        source=SourceType.traefik,
        external_id="dashboard",
        name="dashboard",
        traefik_router="dashboard@docker",
        urls=["https://dash.lan"],
        auth_mode=AuthMode.basic,
    )
    store.reconcile(SourceType.traefik, [traefik_item], stale_threshold=3)
    svc = store.get_service("dashboard")
    assert svc is not None
    assert svc.auth_mode == AuthMode.basic
