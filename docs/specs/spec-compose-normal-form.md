# Spec: Compose File Normal Form

Defines the canonical form a `nodes/{node}/{stack}/compose.yaml` file in the operator's
private homelab repo must take, and what the normalization engine (`normalization/`) does
and does not do to bring a file into that form. Consumed by
`src/registry_mcp/normalization/rules.py`, which is the executable version of this document
— rule IDs here match rule IDs there exactly.

## Scope

**In scope:** files matching `nodes/*/*/compose.yaml` (the `NORMALIZATION_PATH_GLOB`
setting), plus the misnamed variants covered by N-100 below. This is the two-level
`{node}/{stack}/` layout the [ADR-003 Repo Structure Contract](../ARDs/ADR-003-OOBE-Decisions.md)
defines and `ansible/roles/docker-stack-deploy/tasks/main.yml` hard-asserts.

**Out of scope (v1):** Traefik dynamic config files (`nodes/*/traefik/dynamic/*.yml`) and
any other non-compose YAML. ADR-001 and ADR-003 currently disagree on where these live and
what they're named — see "Open question" below. Normalizing them is deferred until that's
settled in an ADR, not silently absorbed here.

**Label scope:** every label namespace in a compose file — `traefik.*`, `com.docker.*`,
`org.opencontainers.*`, or any other tool's labels — is normalized by the same structural rules (N-007
through N-009). Normalization has no per-app special-casing; it operates on YAML shape, not
on what a label means to the container reading it.

## The equivalence guarantee

This is the safety mechanism that makes auto-fix responsible, mirroring the confidence/YAML
gates `proposal/generator.py` already applies to security patches.

A normalization rewrite may only ever change **representation**, never **behavior**. Before
any Tier 1 (or Tier 3) change is committed, the engine checks:

```
canonical_projection(parse(before)) == canonical_projection(parse(after))
```

`canonical_projection` applies the known-equivalent structural transforms (a labels list
becomes a labels mapping, a port number becomes its string form, an environment list becomes
a mapping) to **both sides** before comparing, so the rules that deliberately change parsed
shape (N-007, N-010, N-011) don't trip their own gate. Any other difference — a changed key,
a changed value, a dropped service — fails the gate and the file is not committed, regardless
of whether the rewrite came from the deterministic formatter or the DSPy escalation path.

## Tier 1 — auto-fixed (formatting; behavior-preserving)

| ID | Rule |
|---|---|
| N-001 | 2-space indentation; sequence items indented under their key |
| N-002 | No tab characters |
| N-003 | Exactly one trailing newline; no trailing whitespace on any line; no leading blank lines |
| N-004 | No top-level `version:` key (obsolete in the Compose Spec) |
| N-005 | Top-level key order: `services`, `volumes`, `networks`, `configs`, `secrets`, then any remaining keys alphabetically |
| N-006 | Per-service key order: `image`, `container_name`, `restart`, `depends_on`, `env_file`, `environment`, `command`, `entrypoint`, `ports`, `volumes`, `networks`, `labels`, `healthcheck`, `deploy`, then any remaining keys alphabetically |
| N-007 | `labels:` expressed as a mapping (`key: "value"`), never a list (`- "key=value"`) — applies to every label namespace, not just Traefik's |
| N-008 | Label values are quoted strings (`"true"`, `"8765"`), matching how Docker treats them regardless of the YAML scalar type written |
| N-009 | Labels sorted lexicographically within each namespace prefix (e.g. all `traefik.http.routers.*` together, alphabetized) |
| N-010 | Port mappings written as quoted strings (`"8080:80"`), never bare numbers — avoids YAML's base-60 (`8080:80` → sexagesimal) parse trap |
| N-011 | `environment:` expressed as a mapping, never a list of `KEY=value` strings |
| N-012 | Comments are preserved verbatim and stay attached to the key/line they annotated — including the `# temporary` convention [SOP-001](../SOPs/SOP-001-Deploy-New-Service.md) requires on temporary `ports:` mappings |
| N-013 | No `---` YAML document-start marker |

## Tier 2 — reported, never auto-fixed

These require judgment the formatter doesn't have. The engine records each as a finding
(returned from `proposal_normalize` and listed in the sweep's PR body) and leaves the file
untouched.

| ID | Finding | Rationale |
|---|---|---|
| R-001 | Image reference has tag `:latest` or no tag at all | [SOP-001](../SOPs/SOP-001-Deploy-New-Service.md): "Reference the image by explicit version tag, never `latest`" |
| R-002 | Service defines a `build:` key | SOP-001: "Do not include a `build:` key" |
| R-003 | Service has no `restart:` policy | House style — services should survive a host reboot |
| R-004 | `ports:` present without an accompanying `# temporary` comment | SOP-001: ports are a pre-Traefik interim state and must be flagged as such |
| R-005 | Proxy network is a literal string instead of `${PROXY_NETWORK:-proxy-net}` | [ADR-003](../ARDs/ADR-003-OOBE-Decisions.md): "`compose.yaml` uses `${PROXY_NETWORK:-proxy-net}` for the proxy network name, never a hardcoded string" |
| R-006 | A value looks like a hardcoded secret (reuses the `_CREDENTIAL_RE` heuristic from `proposal/generator.py`) | ADR-003: "`compose.yaml` never contains hardcoded secrets" |
| R-007 | A service's `container_name` differs from its service key | House style — avoids two names for one container |

## Tier 3 — rename (auto-fixed, flagged loudly)

| ID | Rule |
|---|---|
| N-100 | A stack directory containing `docker-compose.yml`, `docker-compose.yaml`, or `compose.yml` (but no `compose.yaml`) has that file renamed to `compose.yaml`. |

`ansible/roles/docker-stack-deploy/tasks/main.yml` resolves the compose path as a hard-coded
literal `{{ node }}/{{ service }}/compose.yaml` and fails the deploy task if that exact path
doesn't exist — it does not fall back to any other filename. A stack under any other name is
**invisible to the GitOps deploy pipeline entirely**. Applying N-100 therefore doesn't just
reformat a file — it can make a stack deployable for the first time. This rule is gated by
its own setting (`NORMALIZATION_RENAME_MISNAMED`, default off) and, when it fires, the PR
body must say explicitly which stacks just became deploy-visible, separately from the
cosmetic diff summary.

Mechanically: commit the file content at the new `compose.yaml` path, then delete the old
path, both on the same branch — never the reverse order, so a failure between the two steps
leaves both copies present rather than neither.

## PR batching

One pull request per node, not one per sweep and not one per file. `.github/workflows/deploy.yml`
diffs `nodes/**/compose.yaml` on merge and runs `docker compose pull && up -d` for every
`{node}/{service}` pair with a changed file — so a single PR spanning every node in the repo
would fan out to every stack redeploying at once on merge. Per-node batching bounds that
blast radius to one host per merge and lets an operator stage rollout node by node.
`NORMALIZATION_MAX_FILES_PER_PR` caps how many files one node's PR may contain, so a first
run against a very messy node doesn't produce an unreviewable diff.

A normalization PR never carries a security remediation, and vice versa — they are always
opened as separate PRs, per the existing `.env.example` convention for the write path.

## Escalation path

Tier 1 rules are applied deterministically by `normalization/formatter.py` using a
`ruamel.yaml` round-trip, which preserves comments and only touches what the rules above
name. When the formatter cannot produce a result that passes the equivalence guarantee (an
edge case in the source YAML it doesn't handle), the file is escalated to the DSPy
`NormalizeConfigFile` module (`normalization/generator.py`), which is held to the identical
gate chain the security remediation path uses: credential scrub, confidence threshold,
non-empty output, YAML validity — plus the equivalence guarantee above, which the security
path does not need since it's intentionally changing behavior. A file that fails every gate
in both paths is recorded as a rejected finding and left alone; there is no rule-based
fallback that hand-writes a fix.

## Open question

ADR-001 places Traefik's dynamic config at `nodes/<node>/core/traefik/dynamic/*.yml` (three
path segments, `.yml` extension); ADR-003's Repo Structure Contract defines only the
two-segment `nodes/{node}/{stack}/` form. Bringing Traefik dynamic configs into
normalization's scope requires resolving that conflict first — likely by amending ADR-001 to
match ADR-003, since ADR-003 is the actively-enforced contract (the Ansible role and deploy
workflow both assert its shape). Tracked as follow-up work, not addressed by this spec.
