"""Unit tests for the registry store CRUD."""

import pytest

from registry_mcp.models import Category, Service
from registry_mcp.registry import DuplicateServiceError


def _make(name="plex", **kw):
    defaults = {"display_name": name.title(), "category": Category.media}
    defaults.update(kw)
    return Service(name=name, **defaults)


def test_create_and_get_by_id_and_name(store):
    created = store.create_service(_make())
    assert store.get_service(created.id).name == "plex"
    assert store.get_service("plex").id == created.id


def test_create_duplicate_name_raises(store):
    store.create_service(_make())
    with pytest.raises(DuplicateServiceError):
        store.create_service(_make())


def test_get_missing_returns_none(store):
    assert store.get_service("nope") is None


def test_list_filters(store):
    store.create_service(_make("plex", category=Category.media, host="a", tags=["x"]))
    store.create_service(_make("gitea", category=Category.infra, host="b", tags=["y"]))
    assert {s.name for s in store.list_services()} == {"plex", "gitea"}
    assert [s.name for s in store.list_services(category="media")] == ["plex"]
    assert [s.name for s in store.list_services(host="b")] == ["gitea"]
    assert [s.name for s in store.list_services(tag="x")] == ["plex"]


def test_update_patches_only_given_fields(store):
    created = store.create_service(_make(notes="before"))
    updated = store.update_service(
        created.id,
        {"notes": "after", "display_name": None, "tags": ["new"]},
    )
    assert updated.notes == "after"
    assert updated.display_name == "Plex"  # None left unchanged
    assert updated.tags == ["new"]
    assert updated.updated_at >= created.created_at


def test_update_missing_returns_none(store):
    assert store.update_service("missing", {"notes": "x"}) is None


def test_delete(store):
    created = store.create_service(_make())
    assert store.delete_service(created.id) is True
    assert store.get_service(created.id) is None
    assert store.delete_service(created.id) is False
