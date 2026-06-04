"""Git providers and their factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from registry_mcp.providers.git.base import GitError, GitProvider, OpenedPR
from registry_mcp.providers.git.gitea import GiteaGitProvider

if TYPE_CHECKING:
    from registry_mcp.config import Settings

__all__ = [
    "GitError",
    "GitProvider",
    "OpenedPR",
    "GiteaGitProvider",
    "build_git_provider",
]


def build_git_provider(settings: Settings) -> GitProvider | None:
    """Construct the configured Git provider, or None when the write path is
    not configured (no token / base URL). A None provider disables proposals."""
    if not (settings.git_base_url and settings.git_token and settings.git_repo):
        return None
    if settings.git_provider == "gitea":
        return GiteaGitProvider(settings.git_base_url, settings.git_token)
    # GitHub/GitLab providers are a follow-up increment; fall back to disabled.
    return None
