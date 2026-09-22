"""Deterministic extraction of runtime requirements from a repo's own files.

No LLM runs here, by design — the same discipline that keeps `reconcile.py`
detection-only. Everything in this module is a fact the file states outright:
a port it EXPOSEs, a variable it declares, a volume it mounts. Anything that
needs judgment ("is this env var required?", "does this need a database?") is
left to the confidence-gated `InferServiceRequirements` reasoning module,
which can only add to what this produced, never overrule it.

When the two sources disagree, the compose file wins: a Dockerfile describes
how an image is built, a compose file describes how it is actually run.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field

import yaml

_DEFAULT_PROTOCOL = "tcp"


@dataclass
class ServiceRequirements:
    """What a repo needs in order to run, as stated by its own files."""

    base_image: str | None = None
    # Container-side ports, normalized to "80/tcp". The published/host side is
    # a deployment choice this homelab makes later, not a property of the repo.
    ports: list[str] = field(default_factory=list)
    # name -> declared default; None when declared with no value (compose's
    # pass-through-from-host form), which is a strong hint the operator supplies it.
    env_vars: dict[str, str | None] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    service_names: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    # Parse problems worth surfacing (unreadable compose, etc.) rather than
    # letting an empty result look like a repo that simply needs nothing.
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "base_image": self.base_image,
            "ports": self.ports,
            "env_vars": self.env_vars,
            "volumes": self.volumes,
            "depends_on": self.depends_on,
            "service_names": self.service_names,
            "sources": self.sources,
            "warnings": self.warnings,
        }


def _normalize_port(value: str) -> str | None:
    """Reduce a port spec to the container side, as "80/tcp".

    Handles "80", "8080:80", "127.0.0.1:8080:80" and any of those with a
    "/udp" suffix — the container port is always the last colon-separated
    field, whatever the host-side binding looks like.
    """
    text = str(value).strip()
    if not text:
        return None
    proto = _DEFAULT_PROTOCOL
    if "/" in text:
        text, _, raw_proto = text.rpartition("/")
        proto = raw_proto.strip().lower() or _DEFAULT_PROTOCOL
    container_port = text.split(":")[-1].strip()
    if not container_port:
        return None
    return f"{container_port}/{proto}"


def _dockerfile_lines(content: str) -> list[str]:
    """Logical Dockerfile lines: continuations joined, comment lines dropped."""
    lines: list[str] = []
    buffer = ""
    for raw in content.splitlines():
        stripped = raw.strip()
        # Only a whole-line `#` is a comment; a `#` inside a value is data.
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        buffer += stripped
        if buffer:
            lines.append(buffer)
        buffer = ""
    if buffer:
        lines.append(buffer)
    return lines


def _parse_env_instruction(argument: str) -> dict[str, str | None]:
    """Parse both ENV forms: `ENV A=1 B=2` and legacy `ENV A some value`."""
    try:
        tokens = shlex.split(argument)
    except ValueError:
        tokens = argument.split()
    if not tokens:
        return {}
    if "=" not in tokens[0]:
        # Legacy form: everything after the first token is one value.
        return {tokens[0]: " ".join(tokens[1:]) or None}
    out: dict[str, str | None] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep and key:
            out[key] = value
    return out


def _parse_volume_instruction(argument: str) -> list[str]:
    text = argument.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed if str(item).strip()]
    return [part for part in text.split() if part]


def parse_dockerfile(content: str) -> ServiceRequirements:
    """Extract FROM / EXPOSE / ENV / VOLUME from a Dockerfile."""
    req = ServiceRequirements(sources=["Dockerfile"])
    stage_images: dict[str, str] = {}
    last_from: str | None = None

    for line in _dockerfile_lines(content):
        instruction, _, argument = line.partition(" ")
        keyword = instruction.strip().upper()
        argument = argument.strip()
        if not argument:
            continue

        if keyword == "FROM":
            tokens = argument.split()
            image = tokens[0]
            if len(tokens) >= 3 and tokens[1].upper() == "AS":
                stage_images[tokens[2].lower()] = image
            last_from = image
        elif keyword == "EXPOSE":
            for token in argument.split():
                port = _normalize_port(token)
                if port and port not in req.ports:
                    req.ports.append(port)
        elif keyword == "ENV":
            req.env_vars.update(_parse_env_instruction(argument))
        elif keyword == "VOLUME":
            for volume in _parse_volume_instruction(argument):
                if volume not in req.volumes:
                    req.volumes.append(volume)

    if last_from is not None:
        # A multi-stage final `FROM builder` names a stage, not an image —
        # resolve it back to the image that stage was built from.
        req.base_image = stage_images.get(last_from.lower(), last_from)
    return req


def _compose_env(raw) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            out[str(key)] = None if value is None else str(value)
    elif isinstance(raw, list):
        for entry in raw:
            key, sep, value = str(entry).partition("=")
            if key:
                out[key.strip()] = value if sep else None
    return out


def _compose_volume_target(entry) -> str | None:
    if isinstance(entry, dict):
        target = entry.get("target")
        return str(target) if target else None
    text = str(entry).strip()
    if not text:
        return None
    parts = text.split(":")
    # "name:/data" and "/host:/data:ro" both put the container path second;
    # a lone "/data" is already the container path.
    return parts[1] if len(parts) >= 2 else parts[0]


def _compose_port(entry) -> str | None:
    if isinstance(entry, dict):
        target = entry.get("target")
        if target is None:
            return None
        proto = str(entry.get("protocol") or _DEFAULT_PROTOCOL).lower()
        return f"{target}/{proto}"
    return _normalize_port(entry)


def parse_compose(content: str) -> ServiceRequirements:
    """Extract image/ports/environment/volumes/depends_on from a compose file.

    Aggregates across every service in the file: a repo shipping an app plus
    its database states both as requirements, and which one is "the" service
    is a placement decision for a later phase, not a parsing one.
    """
    req = ServiceRequirements(sources=["compose"])
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        req.warnings.append(f"compose file could not be parsed as YAML: {exc}")
        return req
    if not isinstance(doc, dict):
        req.warnings.append("compose file is not a YAML mapping")
        return req
    services = doc.get("services")
    if not isinstance(services, dict):
        req.warnings.append("compose file declares no `services` mapping")
        return req

    for name, service in services.items():
        req.service_names.append(str(name))
        if not isinstance(service, dict):
            continue
        image = service.get("image")
        if image and req.base_image is None:
            req.base_image = str(image)
        for entry in service.get("ports") or []:
            port = _compose_port(entry)
            if port and port not in req.ports:
                req.ports.append(port)
        for key, value in _compose_env(service.get("environment")).items():
            req.env_vars.setdefault(key, value)
        for entry in service.get("volumes") or []:
            target = _compose_volume_target(entry)
            if target and target not in req.volumes:
                req.volumes.append(target)
        depends = service.get("depends_on")
        names = depends.keys() if isinstance(depends, dict) else (depends or [])
        for dep in names:
            if str(dep) not in req.depends_on:
                req.depends_on.append(str(dep))
    return req


def merge(dockerfile: ServiceRequirements | None, compose: ServiceRequirements | None):
    """Combine both sources, letting compose win where they disagree.

    A Dockerfile says how the image is built; a compose file says how it is
    actually run. Where only the Dockerfile knows something (an EXPOSE the
    compose file never publishes, an ENV default it never overrides), that
    still carries — it is additive, not contradictory.
    """
    merged = ServiceRequirements()
    ordered = [source for source in (compose, dockerfile) if source is not None]
    if not ordered:
        return merged

    for source in ordered:
        if merged.base_image is None:
            merged.base_image = source.base_image
        for port in source.ports:
            if port not in merged.ports:
                merged.ports.append(port)
        for key, value in source.env_vars.items():
            # setdefault keeps compose's value when both declare the same key.
            merged.env_vars.setdefault(key, value)
        for volume in source.volumes:
            if volume not in merged.volumes:
                merged.volumes.append(volume)
        for dep in source.depends_on:
            if dep not in merged.depends_on:
                merged.depends_on.append(dep)
        for name in source.service_names:
            if name not in merged.service_names:
                merged.service_names.append(name)
        merged.sources.extend(source.sources)
        merged.warnings.extend(source.warnings)
    return merged


def parse_snapshot(snapshot) -> ServiceRequirements:
    """Parse whatever files `fetch` managed to read out of the repo."""
    dockerfile = parse_dockerfile(snapshot.dockerfile) if snapshot.dockerfile else None
    compose = parse_compose(snapshot.compose) if snapshot.compose else None
    merged = merge(dockerfile, compose)
    if compose is not None and snapshot.compose_path:
        merged.sources = [
            snapshot.compose_path if source == "compose" else source for source in merged.sources
        ]
    if not merged.sources:
        merged.warnings.append(
            "no Dockerfile or compose file found; requirements could not be determined "
            "from the repository's files"
        )
    return merged
