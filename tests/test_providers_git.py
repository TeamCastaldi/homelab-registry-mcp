"""Tests for the Gitea Git provider, driven by an httpx MockTransport."""

import base64

import httpx
import pytest

from conftest import IsolatedSettings
from registry_mcp.providers.git import GiteaGitProvider, build_git_provider
from registry_mcp.providers.git.base import GitError

REPO = "nathan/homelab"


class FakeGitea:
    """Minimal in-memory Gitea API over an httpx MockTransport."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.branches: list[dict] = []
        self.commits: list[dict] = []
        self.pulls: list[dict] = []
        self.labels = [{"id": 7, "name": "homelab-registry-mcp"}]
        self._next_pr = 41

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "POST" and path.endswith("/branches"):
            body = _json(request)
            self.branches.append(body)
            return httpx.Response(201, json={"name": body["new_branch_name"]})
        if "/contents/" in path:
            file_path = path.split("/contents/", 1)[1]
            if method == "GET":
                if file_path not in self.files:
                    return httpx.Response(404, json={"message": "not found"})
                content = base64.b64encode(self.files[file_path].encode()).decode()
                return httpx.Response(
                    200, json={"content": content, "encoding": "base64", "sha": "deadbeef"}
                )
            if method == "PUT":
                body = _json(request)
                self.files[file_path] = base64.b64decode(body["content"]).decode()
                self.commits.append({"path": file_path, **body})
                return httpx.Response(200, json={"content": {"sha": "newsha"}})
        if path.endswith("/labels"):
            if method == "GET":
                return httpx.Response(200, json=self.labels)
            if method == "POST":
                label = {"id": 99, "name": _json(request)["name"]}
                self.labels.append(label)
                return httpx.Response(201, json=label)
        if path.endswith("/pulls") and method == "POST":
            body = _json(request)
            self._next_pr += 1
            pr = {
                "number": self._next_pr,
                "html_url": f"https://git.test/{REPO}/pulls/{self._next_pr}",
                "title": body["title"],
                "labels": [{"name": "homelab-registry-mcp"}] if body.get("labels") else [],
            }
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        if path.endswith("/pulls") and method == "GET":
            return httpx.Response(200, json=[p for p in self.pulls])
        if "/pulls/" in path and method == "PATCH":
            return httpx.Response(200, json={"state": "closed"})
        return httpx.Response(500, json={"message": f"unhandled {method} {path}"})


def _json(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content.decode() or "{}")


def _provider(fake: FakeGitea) -> GiteaGitProvider:
    return GiteaGitProvider("https://git.test", "tok", transport=fake.transport())


async def test_read_file_decodes_base64():
    fake = FakeGitea(files={"nodes/workload-01/plex/compose.yaml": "services:\n  plex: {}\n"})
    provider = _provider(fake)
    content = await provider.read_file(REPO, "nodes/workload-01/plex/compose.yaml", "main")
    assert "plex" in content


async def test_read_missing_file_raises():
    provider = _provider(FakeGitea())
    with pytest.raises(GitError):
        await provider.read_file(REPO, "nope.yaml", "main")


async def test_create_branch():
    fake = FakeGitea()
    await _provider(fake).create_branch(REPO, "patch/x", "main")
    assert fake.branches == [{"new_branch_name": "patch/x", "old_branch_name": "main"}]


async def test_commit_existing_file_sends_sha():
    fake = FakeGitea(files={"f.yaml": "a: 1\n"})
    await _provider(fake).commit_file(REPO, "f.yaml", "a: 2\n", "patch/x", "fix: bump")
    assert fake.files["f.yaml"] == "a: 2\n"
    assert fake.commits[-1]["sha"] == "deadbeef"  # update path includes sha
    assert fake.commits[-1]["branch"] == "patch/x"


async def test_commit_new_file_omits_sha():
    fake = FakeGitea()
    await _provider(fake).commit_file(REPO, "new.yaml", "a: 1\n", "patch/x", "feat: add")
    assert "sha" not in fake.commits[-1]


async def test_open_pr_resolves_label_and_returns_url():
    fake = FakeGitea()
    opened = await _provider(fake).open_pr(
        REPO, "Title", "Body", "patch/x", "main", "homelab-registry-mcp"
    )
    assert opened.url.endswith(f"/pulls/{opened.number}")
    assert fake.pulls[-1]["labels"]  # label was attached


async def test_list_open_prs_filters_by_label():
    fake = FakeGitea()
    await _provider(fake).open_pr(REPO, "A", "b", "patch/a", "main", "homelab-registry-mcp")
    matching = await _provider(fake).list_open_prs(REPO, label="homelab-registry-mcp")
    assert len(matching) == 1
    none = await _provider(fake).list_open_prs(REPO, label="other")
    assert none == []


async def test_http_error_status_raises_giterror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    provider = GiteaGitProvider("https://git.test", "tok", transport=httpx.MockTransport(handler))
    with pytest.raises(GitError):
        await provider.create_branch(REPO, "x", "main")


# --- factory --------------------------------------------------------------


def test_build_git_provider_disabled_without_config():
    assert build_git_provider(IsolatedSettings()) is None


def test_build_git_provider_gitea():
    settings = IsolatedSettings(
        git_provider="gitea",
        git_base_url="https://git.test",
        git_token="tok",
        git_repo=REPO,
    )
    assert isinstance(build_git_provider(settings), GiteaGitProvider)


def test_build_git_provider_github_not_yet_supported():
    settings = IsolatedSettings(
        git_provider="github",
        git_base_url="https://api.github.com",
        git_token="tok",
        git_repo=REPO,
    )
    assert build_git_provider(settings) is None
