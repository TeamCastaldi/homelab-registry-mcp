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

import os
import tempfile
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
    if loaded is None:
        return CommentedMap()
    if not isinstance(loaded, dict):
        raise ValueError(f"inventory {path} is a {type(loaded).__name__}, expected a mapping")
    return loaded


def _child_map(parent: CommentedMap, key: str) -> CommentedMap:
    """`parent[key]` as a mapping, creating it or replacing an explicit null.

    Ansible's YAML inventories routinely write a bare `heimdall:` (a host with
    no vars) or `docker:` (an empty group). ruamel loads those as None, which
    `setdefault()` hands straight back.
    """
    value = parent.get(key)
    if value is None:
        value = CommentedMap()
        parent[key] = value
    if not isinstance(value, dict):
        raise ValueError(f"inventory key {key!r} is a {type(value).__name__}, expected a mapping")
    return value


def _dump(data: CommentedMap, path: Path) -> None:
    """Write atomically: this is the live inventory the deploy workflow reads,
    so a failure partway through must never leave it truncated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _yaml.dump(data, fh)
        if path.exists():
            # mkstemp creates 0600; keep the operator's existing mode.
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


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

    root = _child_map(data, "all")
    host_block = _child_map(_child_map(root, "hosts"), hostname)
    if ansible_host:
        host_block["ansible_host"] = ansible_host

    if groups:
        children = _child_map(root, "children")
        for group in groups:
            group_hosts = _child_map(_child_map(children, group), "hosts")
            if hostname not in group_hosts:
                group_hosts[hostname] = CommentedMap()

    _dump(data, path)
