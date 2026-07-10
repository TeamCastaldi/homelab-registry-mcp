# Security Policy

## Supported versions

`homelab-registry-mcp` is released as versioned container images
(`ghcr.io/teamcastaldi/homelab-registry-mcp`) published on `v*` tags. Security
fixes target the latest release line; older tags are not back-patched.

| Version | Supported |
|---------|-----------|
| Latest tagged release | ✅ |
| Older tags | ❌ |

## Reporting a vulnerability

Please report security issues **privately** rather than opening a public issue.

**How to report:**
Use GitHub's private vulnerability reporting:
`Security` tab → `Report a vulnerability`.

**Response time:**
This is a family-maintained project. Expect an initial response within 7 days;
critical issues are prioritized.

## Security posture

This server is built to be conservative by default. Relevant guarantees:

- **Upstream APIs are read-only.** Traefik, Authentik, and Docker are queried,
  never modified. The Docker socket is mounted read-only.
- **Least-privilege tokens.** The Authentik integration expects a **read-only
  service-account token, never an admin token**.
- **The write path writes to Git only.** The proposal layer opens pull requests
  for a human to review and merge; it never merges, deploys, or edits the
  filesystem. All write behavior is **off by default** (`GIT_*` unset,
  `PROPOSAL_AUTO_CREATE=false`).
- **Secrets are redacted in logs.** Any field named `token`, `password`,
  `secret`, `key`, `authorization`, or `api_key` is replaced with
  `***redacted***` before logging.
- **Secrets at rest use `git-crypt`.** The `secrets_*` tools operate on a
  `git-crypt`-encrypted homelab repo; the key is supplied via file or env var and
  never committed in plaintext.
- **LAN-only.** Authentication in front of the MCP endpoint is deferred
  (ForwardAuth breaks MCP clients), so the server is intended to run on a trusted
  LAN behind Traefik. Do not expose it directly to the public internet.

Periodic manual security reviews are kept in
[`docs/Security Reviews/`](docs/Security%20Reviews/).

## Dependency vulnerabilities

Dependabot is enabled and monitors Python (`uv`/pip), Docker base images, and
GitHub Actions dependencies weekly. If you find a vulnerable dependency that
Dependabot hasn't flagged, report it using the process above.
