# Cloud environment settings

Read at the end of `init-project`'s Phase 3, using the install and test commands that phase just wrote into the manifest and CI workflow, and again for Phase 7's restatement.


If this project will run in Claude Code on the web, someone has to fill in the "Add cloud environment" dialog — Network access, Environment variables, Setup script — before the first session can do anything. Left to guesswork, that turns into a slow back-and-forth: a setup script that fails because Network access was left at `None`, an API key pasted into Environment variables because nothing said not to, a script that silently resolves the wrong Python and reports success anyway. Everything needed to get this right the first time was just decided in this phase, so hand it to the user now, as three copy-pasteable blocks with a one-line reason each — not as a description they have to translate themselves.

**Network access**

- Default: **Trusted**. It covers the standard package registries (PyPI, npm, crates.io, RubyGems, the Go module proxy) and GitHub, which is what the setup script below needs to actually install anything.
- Recommend **Custom** only if the stack pulls from somewhere Trusted doesn't reach — a private registry, an internal index, a specific Docker Hub image — and name the exact host(s), not "everything."
- Recommend **None** only when there is truly no install step. Say plainly that it will break the setup script the moment a dependency needs fetching.

**Environment variables**

- Default: leave empty. This field is visible to anyone with access to the environment; it's for non-secret config (`NODE_ENV=development`), not credentials — the dialog says so, and this skill doesn't override it.
- Never suggest putting an API key, token, or password here.
- Only propose an entry if the interview surfaced a real, non-secret variable the setup script or session genuinely needs, and say which one and why.

**Setup script**

- Use the exact install command from the manifest and CI workflow this phase just wrote — not a fresh guess at how to install dependencies.
- Make it self-verifying: end it with one command that fails loudly if the install is broken (`python -c "import <pkg>"`, `npm ls`, `go build ./...`), so a bad script errors at session start instead of surfacing later as an unrelated-looking failure.
- Prefer explicit interpreters (`python3 -m pip install …`, not a bare `pip`) — the base image's default `python`/`pip` on `PATH` isn't guaranteed, and picking the wrong one fails silently rather than erroring.
- This script is bash, not a permissions request — there is no Docker-in-Docker or privileged-mode toggle in this dialog. If the project genuinely needs a Docker daemon inside the session (e.g. testcontainers), flag that to the user as a separate, explicit note rather than implying the setup script can grant it.
