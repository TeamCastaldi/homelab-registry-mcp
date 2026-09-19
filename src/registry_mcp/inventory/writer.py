"""Comment-safe upsert of one host's entry into a YAML Ansible inventory
file (ADR-015).

Uses the same ``ruamel.yaml`` round-trip discipline
``normalization/formatter.py`` established for compose files: load, mutate
only the keys this tool owns, dump. Every other host, group, and comment in
the file is left exactly as it was.

Target shape (the standard Ansible YAML inventory layout)::

    all:
      hosts:
        <hostname>:
          ansible_host: <ip or ansible_host>
      children:
        <group>:
          hosts:
            <hostname>: {}

Only ``ansible_host`` is written into a host's own block; any other host
vars a human has already added there (``ansible_user``, ``ansible_port``,
...) are left untouched. A group's host entry is added only if missing —
an existing ``{}`` or populated block for that host under a group is never
overwritten.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True
_yaml.width = 4096


def _load(path: Path) -> CommentedMap:
    if not path.exists():
        return CommentedMap({"all": CommentedMap({"hosts": CommentedMap()})})
    with path.open("r", encoding="utf-8") as fh:
        loaded = _yaml.load(fh)
    return loaded if loaded is not None else CommentedMap()


def _dump(data: CommentedMap, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        _yaml.dump(data, fh)


def upsert_host(
    path: Path,
    hostname: str,
    ansible_host: str | None,
    groups: list[str],
) -> None:
    """Upsert one host's `ansible_host` and group memberships into the
    inventory file at `path`, creating the file or any missing section as
    needed. Every other entry in the file is preserved as-is."""
    data = _load(path)

    root = data.setdefault("all", CommentedMap())
    hosts = root.setdefault("hosts", CommentedMap())
    host_block = hosts.setdefault(hostname, CommentedMap())
    if ansible_host:
        host_block["ansible_host"] = ansible_host

    if groups:
        children = root.setdefault("children", CommentedMap())
        for group in groups:
            group_block = children.setdefault(group, CommentedMap())
            group_hosts = group_block.setdefault("hosts", CommentedMap())
            if hostname not in group_hosts:
                group_hosts[hostname] = CommentedMap()

    _dump(data, path)
