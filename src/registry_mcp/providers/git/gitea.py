"""Gitea/Forgejo Git provider (the two are API-compatible)."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from registry_mcp.logging import get_logger
from registry_mcp.providers.git.base import GitError, OpenedPR

_log = get_logger("providers.git")


class GiteaGitProvider:
    """Talks to the Gitea REST API (v1). Token needs repo read+write scope."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
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
        url = f"{self._base}/api/v1/{path.lstrip('/')}"
        headers = {
            "Authorization": f"token {self._token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                return await client.request(method, url, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GitError(f"Gitea request {method} {path} failed: {exc}") from exc

    @staticmethod
    def _raise_for(response: httpx.Response, action: str) -> None:
        if response.status_code >= 400:
            raise GitError(f"Gitea {action} returned {response.status_code}: {response.text[:300]}")

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        response = await self._request(
            "POST",
            f"repos/{repo}/branches",
            json={"new_branch_name": branch, "old_branch_name": base},
        )
        self._raise_for(response, "create_branch")

    async def read_file(self, repo: str, path: str, ref: str) -> str:
        response = await self._request("GET", f"repos/{repo}/contents/{path}", params={"ref": ref})
        self._raise_for(response, "read_file")
        payload = response.json()
        content = payload.get("content") or ""
        if payload.get("encoding") == "base64":
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
            body["sha"] = sha  # required by Gitea to update an existing file
        # PUT creates or updates depending on whether sha is present.
        response = await self._request("PUT", f"repos/{repo}/contents/{path}", json=body)
        self._raise_for(response, "commit_file")

    async def _resolve_label_ids(self, repo: str, label: str) -> list[int]:
        """Best-effort: find the label id, creating it if missing."""
        try:
            response = await self._request("GET", f"repos/{repo}/labels")
            self._raise_for(response, "list_labels")
            for item in response.json():
                if item.get("name") == label:
                    return [item["id"]]
            created = await self._request(
                "POST", f"repos/{repo}/labels", json={"name": label, "color": "#0366d6"}
            )
            self._raise_for(created, "create_label")
            return [created.json()["id"]]
        except GitError as exc:
            _log.warning("label_resolution_failed", label=label, error=str(exc))
            return []

    async def open_pr(
        self, repo: str, title: str, body: str, branch: str, base: str, label: str | None = None
    ) -> OpenedPR:
        payload: dict[str, Any] = {"title": title, "body": body, "head": branch, "base": base}
        if label:
            ids = await self._resolve_label_ids(repo, label)
            if ids:
                payload["labels"] = ids
        response = await self._request("POST", f"repos/{repo}/pulls", json=payload)
        self._raise_for(response, "open_pr")
        data = response.json()
        return OpenedPR(url=data.get("html_url", ""), number=int(data.get("number", 0)))

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
        in the Gitea API, so this reads the issue comments endpoint — it does
        not include inline review comments on specific diff lines."""
        comments: list[dict] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                f"repos/{repo}/issues/{number}/comments",
                params={"limit": 100, "page": page},
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
