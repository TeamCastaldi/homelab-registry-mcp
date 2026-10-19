# SOP: Enable the Gated Dockhand Container and Stack Actions

**Owner:** the maintainer  
**Frequency:** Once, then again after rotating the Dockhand token  
**Last Updated:** 2026  
**Status:** Steps 3, 5, and 6 run against the live Dockhand on 2026-10-19 for container actions (restart of bentopdf: HTTP 200, ran once; wrong answer and expiry both refused). Stack actions not yet run (#201)

---

### Purpose

Let an MCP client start, stop, restart, or redeploy (forced recreate) one Dockhand
container or stack, with the operator solving a math challenge for every action.
See [ADR-023](../ADRs/ADR-023-Dockhand-Gated-Container-And-Stack-Actions.md) for what
is and is not allowed.

Everything else Dockhand's API can do stays unreachable from this server: deletes,
stack `down`, `exec`, bulk operations, image/volume/network/backup calls.

---

### When to use it

- You want to act on a service from the conversation where you diagnosed it.
- Not for deploying a new service. That still goes through a merged PR and the CD
  pipeline.

---

### Prerequisites

- The registry already talks to Dockhand: `DOCKHAND_API_URL` and `DOCKHAND_TOKEN` set,
  and `dockhand_list_environments` returns your environments.
- A Dockhand user whose role may control containers and stacks on the environments
  you care about, and nothing wider. Dockhand's manual does not say whether an API
  token carries its own scope or inherits its owner's role, so check in Step 5.
- Optional but recommended: `FEAT_API_DOCS=true` on the Dockhand container, so
  `/api/docs` serves the live OpenAPI document.

---

### Steps

1. **Pick the token.** In Dockhand: Profile, API tokens, Generate token. Use a user
   with only the control rights you need. Today the registry uses one token for reads
   and writes (ADR-023, open item 3), so this token replaces `DOCKHAND_TOKEN`.
2. **Set the flag.** In the registry's environment (for the maintainer's deployment, in
   Infisical, since Dockhand injects secrets at deploy time):

   ```
   DOCKHAND_ALLOW_WRITE=true
   # optional
   DOCKHAND_ACTION_CHALLENGE_TTL_MINUTES=5
   DOCKHAND_ACTION_TIMEOUT_SECONDS=120
   ```

3. **Redeploy the registry** through Dockhand (not a raw `docker compose up`, which
   falls back to a placeholder `.env`). Then call `config_status`: "Dockhand actions"
   must be listed as on with nothing missing.
4. **Check the live API.** Open `https://<dockhand>/api/docs/ui` and confirm these
   exist and take the shape ADR-023 records:
   - `POST /api/containers/{id}/start|stop|restart`
   - `POST /api/stacks/{name}/start|stop|restart|deploy` with `?env=`
   - the `deploy` body: `{"pull": bool, "build": bool, "forceRecreate": bool}`

   If a path or parameter differs, stop and fix the client before going on.
5. **First action on something harmless.** Pick a container nobody depends on:
   1. `dockhand_list_environments`, then note the environment id.
   2. `dockhand_container_action_request(environment_id, container_id, "restart")`.
      Read the action it prints back. Nothing has run.
   3. Solve the sum and call `dockhand_action_confirm(request_id, answer)`.
   4. Confirm the container restarted in Dockhand. A 401 or 403 here means the token's
      user lacks the right; fix the role, not the code.
6. **Try the refusals.** A wrong answer must kill the challenge (a second confirm says
   `wrong_answer`), and a challenge left past the TTL must say `expired`.

---

### Verification

- `config_status` lists "Dockhand actions" and reports no problems.
- Step 5 restarted exactly the container you named, once.
- The kept row records it: the challenge's status is `executed` with a result like
  `HTTP 200` (the `pendingdockhandaction` table in the registry's SQLite).

---

### Cautions

- **Do not act on Dockhand's own container or the registry's own.** A restart drops
  the connection mid-confirm, and a stop leaves you without the tool to start it
  again. The tools do not stop you (ADR-023, open item 2).
- A stack **redeploy recreates every container in the stack**, and `pull=true` can
  change the version that runs. A stack **stop** stops all of it.
- The math challenge is friction against a mistake, not a security boundary: the sum
  is printed in the challenge. The real controls are the flag and the token's role.
- A timeout or dropped connection reports that the action "may or may not have been
  applied". It is never retried. Check the container in Dockhand before asking again.

---

### Rollback

Set `DOCKHAND_ALLOW_WRITE=false` (or remove it) and redeploy the registry. The three
tools disappear from the server. To revoke at the source, delete the token in
Dockhand (Profile, API tokens); reads then also fail until you issue a new one.

---

### Escalation

- Every action returns 404: Step 4 found a path mismatch. Compare with `/api/docs`.
- 401/403: the token's user lacks the right, or the token was deleted or has expired.
- 429 or repeated 401: Dockhand rate-limits failed authentication per IP (15 failures
  a minute, then a five-minute cooldown). Wait it out.
- A container restarted but the tool reported an error: Dockhand finished after the
  registry gave up. Check the container, and raise `DOCKHAND_ACTION_TIMEOUT_SECONDS`.
