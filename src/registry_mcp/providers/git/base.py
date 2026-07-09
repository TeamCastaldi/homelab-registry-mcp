"""GitProvider protocol and the shared open-PR result shape."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class GitError(RuntimeError):
    """Raised when a Git provider API call fails."""


class OpenedPR(BaseModel):
    """The result of opening a pull request."""

    url: str
    number: int


@runtime_checkable
class GitProvider(Protocol):
    """Minimal Git hosting surface the proposal engine depends on.

    All methods operate on ``repo`` in ``owner/name`` form. Implementations talk
    to a hosting API (Gitea, GitHub, ...) — never to the local filesystem.
    """

    async def create_branch(self, repo: str, branch: str, base: str) -> None: ...

    async def read_file(self, repo: str, path: str, ref: str) -> str: ...

    async def commit_file(
        self, repo: str, path: str, content: str, branch: str, message: str
    ) -> None: ...

    async def open_pr(
        self, repo: str, title: str, body: str, branch: str, base: str, label: str | None = None
    ) -> OpenedPR: ...

    async def list_open_prs(self, repo: str, label: str | None = None) -> list[dict]: ...

    async def close_pr(self, repo: str, number: int) -> None: ...

    async def list_pr_comments(self, repo: str, number: int) -> list[dict]: ...
