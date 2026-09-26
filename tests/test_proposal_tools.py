"""Tests for the proposal MCP tool surface (graceful when write path is off)."""

from conftest import IsolatedSettings, tool_payload
from registry_mcp.models import FindingType, Proposal
from registry_mcp.proposal import ProposalStore
from registry_mcp.registry import RegistryStore
from registry_mcp.server import build_server


async def call(server, name, args):
    return tool_payload(await server.call_tool(name, args))


def _server(tmp_path):
    return build_server(IsolatedSettings(registry_db_path=str(tmp_path / "r.db")))


def _healthy_server(tmp_path):
    """A server whose startup health checks all pass (read_only=False), so tests
    below exercise the write-path config guard rather than the read-only gate."""
    repo = tmp_path / "homelab"
    (repo / ".git").mkdir(parents=True)
    ansible_cfg = tmp_path / "ansible.cfg"
    ansible_cfg.write_text("")
    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("")
    return build_server(
        IsolatedSettings(
            registry_db_path=str(tmp_path / "r.db"),
            secrets_repo_path=str(repo),
            ansible_cfg_path=str(ansible_cfg),
            ssh_key_path=str(ssh_key),
        )
    )


async def test_proposal_create_disabled_returns_error(tmp_path):
    server = _healthy_server(tmp_path)
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    result = await call(server, "proposal_create", {"service_id": added["id"]})
    assert "error" in result
    assert "write path not configured" in result["error"]


async def test_proposal_create_read_only_when_health_checks_fail(tmp_path):
    server = _server(tmp_path)
    added = await call(server, "registry_add_service", {"name": "plex", "display_name": "Plex"})
    result = await call(server, "proposal_create", {"service_id": added["id"]})
    assert "error" in result
    assert "read-only mode" in result["error"]


async def test_proposal_cancel_read_only_when_health_checks_fail(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_cancel", {"proposal_id": "whatever"})
    assert "error" in result
    assert "read-only mode" in result["error"]


async def test_proposal_list_open_empty(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_list_open", {})
    assert result["items"] == []


def _seed_open_proposal(tmp_path, **overrides) -> Proposal:
    """Seed a real open Proposal directly into the same SQLite file `_server(tmp_path)`
    builds its server against (mirrors the RegistryStore/AdoptionDraftStore seeding
    pattern in test_server_runtime.py's SR2/G3 tests)."""
    db_path = str(tmp_path / "r.db")
    store = RegistryStore(db_path)
    proposals = ProposalStore(store.engine)
    fields = dict(
        service_id="svc-1",
        finding_type=FindingType.image_update,
        pr_url="https://git.test/nathan/homelab/pulls/7",
        pr_number=7,
        branch="registry-mcp/svc-1-image-update",
        file_path="nodes/workload-01/plex/compose.yaml",
        diff="services:\n  plex:\n    image: plex:1.2.4\n",
        confidence=0.92,
    )
    fields.update(overrides)
    return proposals.create(Proposal(**fields))


async def test_proposal_list_open_returns_seeded_open_proposal(tmp_path):
    """PT1: `proposal_list_open` returning `[]` unconditionally (dropping the
    query entirely, or querying the wrong table/status) would pass the
    empty-case test above just as well — only a real open Proposal in the
    database can catch that."""
    seeded = _seed_open_proposal(tmp_path)
    server = _server(tmp_path)

    result = await call(server, "proposal_list_open", {})

    assert [item["id"] for item in result["items"]] == [seeded.id]
    item = result["items"][0]
    assert item["service_id"] == "svc-1"
    assert item["finding_type"] == "image_update"
    assert item["pr_url"] == "https://git.test/nathan/homelab/pulls/7"
    assert item["file_path"] == "nodes/workload-01/plex/compose.yaml"


async def test_proposal_get_missing(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_get", {"proposal_id": "nope"})
    assert "error" in result


async def test_proposal_get_returns_full_detail_for_seeded_proposal(tmp_path):
    """PT2: `proposal_get` always answering "not found" would pass the
    missing-id test above just as well — only a real proposal id round-tripped
    through the tool can catch that."""
    seeded = _seed_open_proposal(tmp_path, diff="services:\n  plex:\n    image: plex:1.2.4\n")
    server = _server(tmp_path)

    result = await call(server, "proposal_get", {"proposal_id": seeded.id})

    assert "error" not in result
    assert result["id"] == seeded.id
    assert result["pr_number"] == 7
    assert result["branch"] == "registry-mcp/svc-1-image-update"
    assert result["diff"] == "services:\n  plex:\n    image: plex:1.2.4\n"


async def test_proposal_verify_missing_service(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_verify", {"service_id": "ghost"})
    assert "error" in result


async def test_proposal_normalize_read_only_when_health_checks_fail(tmp_path):
    server = _server(tmp_path)
    result = await call(server, "proposal_normalize", {})
    assert "error" in result
    assert "read-only mode" in result["error"]


async def test_proposal_normalize_disabled_returns_error(tmp_path):
    server = _healthy_server(tmp_path)
    result = await call(server, "proposal_normalize", {})
    assert "error" in result
    assert "write path not configured" in result["error"]
