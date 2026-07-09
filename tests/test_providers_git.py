"""Tests for the Gitea Git provider, driven by an httpx MockTransport."""

import base64

import httpx
import pytest

from conftest import IsolatedSettings
from registry_mcp.providers.git import GiteaGitProvider, GitHubGitProvider, build_git_provider
from registry_mcp.providers.git.base import GitError

REPO = "nathan/homelab"


class FakeGitea:
    """Minimal in-memory Gitea API over an httpx MockTransport."""

    def __init__(
        self, files: dict[str, str] | None = None, comments: dict[int, list] | None = None
    ):
        self.files = dict(files or {})
        self.branches: list[dict] = []
        self.commits: list[dict] = []
        self.pulls: list[dict] = []
        self.labels = [{"id": 7, "name": "homelab-registry-mcp"}]
        self.comments: dict[int, list[dict]] = dict(comments or {})
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
        if "/issues/" in path and path.endswith("/comments") and method == "GET":
            number = int(path.split("/issues/", 1)[1].split("/comments")[0])
            return httpx.Response(200, json=self.comments.get(number, []))
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


async def test_list_pr_comments_returns_comments_for_that_pr():
    fake = FakeGitea(comments={41: [{"id": 1, "user": {"login": "nathan"}, "body": "looks good"}]})
    comments = await _provider(fake).list_pr_comments(REPO, 41)
    assert comments == [{"id": 1, "user": {"login": "nathan"}, "body": "looks good"}]


async def test_list_pr_comments_empty_when_none_posted():
    fake = FakeGitea()
    comments = await _provider(fake).list_pr_comments(REPO, 41)
    assert comments == []


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


def test_build_git_provider_github():
    settings = IsolatedSettings(
        git_provider="github",
        git_base_url="https://api.github.com",
        git_token="tok",
        git_repo=REPO,
    )
    assert isinstance(build_git_provider(settings), GitHubGitProvider)


# --- GitHub provider ------------------------------------------------------


class FakeGitHub:
    """Minimal in-memory GitHub REST API over an httpx MockTransport."""

    def __init__(
        self, files: dict[str, str] | None = None, comments: dict[int, list] | None = None
    ):
        self.files = dict(files or {})
        self.refs: list[dict] = []
        self.commits: list[dict] = []
        self.pulls: list[dict] = []
        self.labels_added: list[dict] = []
        self.comments: dict[int, list[dict]] = dict(comments or {})
        self._next_pr = 41

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        # Resolve a branch ref to a commit sha (used before forking a new branch).
        if method == "GET" and "/git/ref/heads/" in path:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if method == "POST" and path.endswith("/git/refs"):
            body = _json(request)
            self.refs.append(body)
            return httpx.Response(201, json={"ref": body["ref"]})
        if "/contents/" in path:
            file_path = path.split("/contents/", 1)[1]
            if method == "GET":
                if file_path not in self.files:
                    return httpx.Response(404, json={"message": "Not Found"})
                content = base64.b64encode(self.files[file_path].encode()).decode()
                return httpx.Response(
                    200, json={"content": content, "encoding": "base64", "sha": "deadbeef"}
                )
            if method == "PUT":
                body = _json(request)
                self.files[file_path] = base64.b64decode(body["content"]).decode()
                self.commits.append({"path": file_path, **body})
                return httpx.Response(200, json={"content": {"sha": "newsha"}})
        if path.endswith("/pulls") and method == "POST":
            body = _json(request)
            self._next_pr += 1
            pr = {
                "number": self._next_pr,
                "html_url": f"https://github.com/{REPO}/pull/{self._next_pr}",
                "title": body["title"],
                "labels": [],
            }
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        if path.endswith("/labels") and method == "POST":
            body = _json(request)
            number = int(path.split("/issues/", 1)[1].split("/labels")[0])
            self.labels_added.append({"number": number, "labels": body["labels"]})
            for pr in self.pulls:
                if pr["number"] == number:
                    pr["labels"] = [{"name": name} for name in body["labels"]]
            return httpx.Response(200, json=[{"name": n} for n in body["labels"]])
        if path.endswith("/pulls") and method == "GET":
            return httpx.Response(200, json=list(self.pulls))
        if "/pulls/" in path and method == "PATCH":
            return httpx.Response(200, json={"state": "closed"})
        if "/issues/" in path and path.endswith("/comments") and method == "GET":
            number = int(path.split("/issues/", 1)[1].split("/comments")[0])
            return httpx.Response(200, json=self.comments.get(number, []))
        return httpx.Response(500, json={"message": f"unhandled {method} {path}"})


def _gh(fake: FakeGitHub) -> GitHubGitProvider:
    return GitHubGitProvider("https://api.github.com", "tok", transport=fake.transport())


async def test_github_read_file_decodes_base64():
    fake = FakeGitHub(files={"nodes/workload-01/plex/compose.yaml": "services:\n  plex: {}\n"})
    content = await _gh(fake).read_file(REPO, "nodes/workload-01/plex/compose.yaml", "main")
    assert "plex" in content


async def test_github_read_missing_file_raises():
    with pytest.raises(GitError):
        await _gh(FakeGitHub()).read_file(REPO, "nope.yaml", "main")


async def test_github_create_branch_forks_from_base_sha():
    fake = FakeGitHub()
    await _gh(fake).create_branch(REPO, "patch/x", "main")
    assert fake.refs == [{"ref": "refs/heads/patch/x", "sha": "basesha"}]


async def test_github_commit_existing_file_sends_sha():
    fake = FakeGitHub(files={"f.yaml": "a: 1\n"})
    await _gh(fake).commit_file(REPO, "f.yaml", "a: 2\n", "patch/x", "fix: bump")
    assert fake.files["f.yaml"] == "a: 2\n"
    assert fake.commits[-1]["sha"] == "deadbeef"  # update path includes sha
    assert fake.commits[-1]["branch"] == "patch/x"


async def test_github_commit_new_file_omits_sha():
    fake = FakeGitHub()
    await _gh(fake).commit_file(REPO, "new.yaml", "a: 1\n", "patch/x", "feat: add")
    assert "sha" not in fake.commits[-1]


async def test_github_open_pr_attaches_label_and_returns_url():
    fake = FakeGitHub()
    opened = await _gh(fake).open_pr(
        REPO, "Title", "Body", "patch/x", "main", "homelab-registry-mcp"
    )
    assert opened.url.endswith(f"/pull/{opened.number}")
    assert fake.labels_added[-1]["labels"] == ["homelab-registry-mcp"]


async def test_github_open_pr_survives_label_failure():
    """A failed label attach must not break PR creation (best-effort)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(
                201, json={"number": 5, "html_url": "https://github.com/x/pull/5"}
            )
        if request.url.path.endswith("/labels"):
            return httpx.Response(422, json={"message": "label does not exist"})
        return httpx.Response(500)

    provider = GitHubGitProvider(
        "https://api.github.com", "tok", transport=httpx.MockTransport(handler)
    )
    opened = await provider.open_pr(REPO, "T", "B", "patch/x", "main", "missing-label")
    assert opened.number == 5


async def test_github_list_open_prs_filters_by_label():
    fake = FakeGitHub()
    await _gh(fake).open_pr(REPO, "A", "b", "patch/a", "main", "homelab-registry-mcp")
    matching = await _gh(fake).list_open_prs(REPO, label="homelab-registry-mcp")
    assert len(matching) == 1
    none = await _gh(fake).list_open_prs(REPO, label="other")
    assert none == []


async def test_github_close_pr():
    fake = FakeGitHub()
    opened = await _gh(fake).open_pr(REPO, "T", "B", "patch/x", "main")
    # FakeGitHub returns 200 for the PATCH; close must complete without raising.
    await _gh(fake).close_pr(REPO, opened.number)


async def test_github_close_pr_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    provider = GitHubGitProvider(
        "https://api.github.com", "tok", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GitError):
        await provider.close_pr(REPO, 999)


async def test_github_http_error_status_raises_giterror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    provider = GitHubGitProvider(
        "https://api.github.com", "tok", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GitError):
        await provider.create_branch(REPO, "x", "main")


def test_github_provider_defaults_base_url_when_none():
    provider = GitHubGitProvider(None, "tok")
    assert provider._base == "https://api.github.com"


async def test_github_list_pr_comments_returns_comments_for_that_pr():
    fake = FakeGitHub(
        comments={41: [{"id": 1, "user": {"login": "nathan"}, "body": "please revert this"}]}
    )
    comments = await _gh(fake).list_pr_comments(REPO, 41)
    assert comments == [{"id": 1, "user": {"login": "nathan"}, "body": "please revert this"}]


async def test_github_list_pr_comments_empty_when_none_posted():
    fake = FakeGitHub()
    comments = await _gh(fake).list_pr_comments(REPO, 41)
    assert comments == []
