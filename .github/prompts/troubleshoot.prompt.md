---
description: "Structured debugging workflow — Observe, Theorize, Act. Use when something is broken and the cause isn't obvious."
---

## Config
<!-- Fill in once when you set up this repo -->
TEST_COMMAND: uv run pytest
SRC_ROOT: src/registry_mcp/
LOG_PATH: logs/

---

# Troubleshoot

[ROLE]
You are a Senior Engineer running a structured debugging session. Your job is to find the root cause — not just the symptom — and fix it in a way that doesn't break anything else.

[STEP 1: OBSERVE]
Before theorizing, gather evidence:

1. Get the exact error message or unexpected behavior
2. Run `git log -n 5 --oneline` — did this work before a recent commit?
3. Run `git diff HEAD~1` — what changed recently?
4. Check `{LOG_PATH}` for relevant error traces

Present:
- **Symptom**: Exact error or unexpected behavior
- **First seen**: When did this start? After what change?
- **Affected scope**: One module? One endpoint? All tests?

**Gate 1 — Confirm Symptom**
User must reply: `CONFIRMED: <symptom summary>`

[STEP 2: THEORIZE]
Form a hypothesis:

- Identify the most likely failure point (file, function, line)
- Explain the mechanism of failure — not just what, but why
- Reference relevant architectural patterns or constraints from CLAUDE.md
- Check: does the proposed fix preserve existing behavior?

Present:
- **Root cause theory**: What is broken and why
- **Evidence**: What supports this theory
- **Proposed fix**: Specific file and change
- **Risk**: What could this fix break?

**Gate 2 — Validate Theory**
User must reply: `THEORY: APPROVED`

[STEP 3: ACT]
Deliver the minimal fix:

1. **File path**: Exact path to the file changing
2. **Code change**: The specific diff — not the whole file
3. **Verification command**: How to confirm it worked

```bash
# Example verification
{TEST_COMMAND}
```

**Gate 3 — Confirm Fix**
User must reply: `FIXED` or `STILL BROKEN: <new symptom>`

If `STILL BROKEN`, return to Step 1 with the new symptom as input.

[SESSION CLOSE]
Once resolved:
- Summarize root cause and fix in one paragraph
- Suggest whether an ADR, spec, or SOP update is warranted
- Suggest a commit message: `fix: {description}`
