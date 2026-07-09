"""GitHub Git provider (REST API v3).

Implements the same ``GitProvider`` surface as the Gitea provider, talking to
the GitHub REST API. The token needs read+write access to repository contents
and pull requests (classic PATs: the ``repo`` scope; fine-grained tokens: the
Contents and Pull requests repository permissions). Like every Git provider, it
only touches the hosting API — never the local filesystem.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from registry_mcp.logging import get_logger
from registry_mcp.providers.git.base import GitError, OpenedPR

_log = get_logger("providers.git")

# Default public GitHub API root; override via GIT_BASE_URL for GHES.
_DEFAULT_API_ROOT = "https://api.github.com"


class GitHubGitProvider:
    """Talks to the GitHub REST API. ``base_url`` defaults to api.github.com but
    can point at a GitHub Enterprise Server ``/api/v3`` root."""

    def __init__(
        self,
        base_url: str | None,
        token: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = (base_url or _DEFAULT_API_ROOT).rstrip("/")
        self._token = token
        self._timeout = timeout
        self._transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self._base}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                return await client.request(method, url, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GitError(f"GitHub request {method} {path} failed: {exc}") from exc

    @staticmethod
    def _raise_for(response: httpx.Response, action: str) -> None:
        if response.status_code >= 400:
            raise GitError(
                f"GitHub {action} returned {response.status_code}: {response.text[:300]}"
            )

    async def _branch_sha(self, repo: str, branch: str) -> str:
        """Resolve the commit SHA a branch points at (needed to fork a new ref)."""
        response = await self._request("GET", f"repos/{repo}/git/ref/heads/{branch}")
        self._raise_for(response, "resolve_branch")
        return response.json()["object"]["sha"]

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        sha = await self._branch_sha(repo, base)
        response = await self._request(
            "POST",
            f"repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        self._raise_for(response, "create_branch")

    async def read_file(self, repo: str, path: str, ref: str) -> str:
        response = await self._request("GET", f"repos/{repo}/contents/{path}", params={"ref": ref})
        self._raise_for(response, "read_file")
        payload = response.json()
        content = payload.get("content") or ""
        if payload.get("encoding") == "base64":
            # GitHub wraps the base64 payload at 60 chars; b64decode ignores newlines.
            return base64.b64decode(content).decode("utf-8")
        return content

    async def _file_sha(self, repo: str, path: str, branch: str) -> str | None:
        response = await self._request(
            "GET", f"repos/{repo}/contents/{path}", params={"ref": branch}
        )
        if response.status_code == 404:
            return None
        self._raise_for(response, "stat_file")
        return response.json().get("sha")

    async def commit_file(
        self, repo: str, path: str, content: str, branch: str, message: str
    ) -> None:
        sha = await self._file_sha(repo, path, branch)
        body: dict[str, Any] = {
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "message": message,
            "branch": branch,
        }
        if sha is not None:
            body["sha"] = sha  # required by GitHub to update an existing file
        # PUT creates or updates depending on whether sha is present.
        response = await self._request("PUT", f"repos/{repo}/contents/{path}", json=body)
        self._raise_for(response, "commit_file")

    async def _add_label(self, repo: str, number: int, label: str) -> None:
        """Best-effort: attach a label to the PR (PRs are issues for labelling)."""
        try:
            response = await self._request(
                "POST", f"repos/{repo}/issues/{number}/labels", json={"labels": [label]}
            )
            self._raise_for(response, "add_label")
        except GitError as exc:
            _log.warning("label_attach_failed", label=label, number=number, error=str(exc))

    async def open_pr(
        self, repo: str, title: str, body: str, branch: str, base: str, label: str | None = None
    ) -> OpenedPR:
        response = await self._request(
            "POST",
            f"repos/{repo}/pulls",
            json={"title": title, "body": body, "head": branch, "base": base},
        )
        self._raise_for(response, "open_pr")
        data = response.json()
        number = int(data.get("number", 0))
        if label:
            await self._add_label(repo, number, label)
        return OpenedPR(url=data.get("html_url", ""), number=number)

    async def list_open_prs(self, repo: str, label: str | None = None) -> list[dict]:
        response = await self._request("GET", f"repos/{repo}/pulls", params={"state": "open"})
        self._raise_for(response, "list_open_prs")
        prs = response.json()
        if label is None:
            return prs
        return [
            pr for pr in prs if any(lbl.get("name") == label for lbl in (pr.get("labels") or []))
        ]

    async def close_pr(self, repo: str, number: int) -> None:
        response = await self._request(
            "PATCH", f"repos/{repo}/pulls/{number}", json={"state": "closed"}
        )
        self._raise_for(response, "close_pr")

    async def list_pr_comments(self, repo: str, number: int) -> list[dict]:
        """List all comments on a PR, paginating until exhausted. PRs are issues
        in the GitHub API, so this reads the issue comments endpoint — it does
        not include inline review comments on specific diff lines."""
        comments: list[dict] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                f"repos/{repo}/issues/{number}/comments",
                params={"per_page": 100, "page": page},
            )
            self._raise_for(response, "list_pr_comments")
            batch = response.json()
            if not batch:
                break
            comments.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return comments
