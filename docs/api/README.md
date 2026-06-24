# MCP Interface Documentation

This folder contains hand-written documentation for the server's **MCP interface**
— the tools, resources, and prompts it exposes to clients — plus integration
guides. It covers the context that the machine-readable tool schemas can't convey:
intent, workflows, and the "why" behind the surface.

`homelab-registry-mcp` speaks the Model Context Protocol over the streamable-http
transport (also `stdio` / `sse`). It is **not** an HTTP REST/OpenAPI service, so
there is no Swagger / `/docs` page — the authoritative tool list is registered in
[`src/registry_mcp/server.py`](../../src/registry_mcp/server.py).

## What belongs here

- Tool guides that explain the intended workflow behind a tool group (registry
  CRUD, discovery, linking, hardware, secrets, proposals)
- Resource reference: the URIs the server exposes and what they return
  (e.g. `hardware://all`, `hardware://{node_id}`, the Traefik/Authentik resources)
- Prompt reference: the MCP prompts the server ships and when to use them
- Client integration guides (VS Code `.vscode/mcp.json`, Claude Desktop) and
  example tool-call sequences
- Auth / transport notes for consumers

## What does not belong here

- The canonical tool registration (that lives in `src/registry_mcp/server.py`)
- Architecture decisions about the interface (those go in `docs/ARDs/` — see
  `ADR-002-Client-Interfaces.md`)
- Deployment or infrastructure runbooks (those go in `docs/SOPs/`)

## See also

- [`CLAUDE.md`](../../CLAUDE.md) — full tool/resource inventory and conventions
- [`README.md`](../../README.md) — quick-start client connection instructions
