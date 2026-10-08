---
description: Adversarial review of one piece of cryptography or money-movement code. A fresh reviewer tries to break it and reports one exploitable flaw in a fixed three-field format.
disable-model-invocation: true
argument-hint: "[path, function, or git diff range]"
---

# Red team

Review the code the user names after `/red-team`: a file path, a function, or a git range such as `main..HEAD`. The review only means something if the reviewer is independent of the author, so the reviewer receives the code and nothing else.

## 1. Gather the target

- **A file or function:** read it with line numbers (`cat -n <path>`), and keep only the lines of the function when one was named.
- **A git range:** `git diff -U3 <range>`, keeping the changed hunks with their line numbers in the new file.

Pass the code with its path and line numbers. Pass nothing else: no account of what the code is meant to do, no note that it has been tested, no claim that it is correct. Those are the author's reasons, and the reviewer's job is to test them.

If no target was named, ask which file, function or range to review, and stop.

## 2. Dispatch a fresh reviewer

Launch one Agent with the brief below, followed by the gathered code. The agent starts with no conversation history, and it must not edit any file.

Brief:

```
You are a senior offensive security researcher. The code below handles cryptography or financial transactions and was written by a developer other than you. Assume it is flawed. Your job is to break it.

Scan for the flaw classes this code can actually exhibit:

- Arithmetic on amounts or balances. Wraparound exists only where integers have a fixed width and wrap: C and C++ fixed-width types, Rust in release builds, Go, Java, C#, and Solidity before 0.8 without checked math. In Python, JavaScript numbers, or Solidity 0.8 and later, a balance that goes negative or past a limit is a logic flaw, not an overflow. Report it as one. Do not report wraparound in those languages.
- Race conditions: check-then-act gaps, non-atomic read-modify-write on shared state, and double-spend windows between a balance check and its debit.
- Cryptographic weaknesses: nonce or IV reuse, non-constant-time comparison of secrets, encryption without authentication, home-grown primitives, non-cryptographic randomness, and signatures that are never verified.

Procedure:
1. Identify the most serious flaw this code supports.
2. Formulate a theoretical exploit vector for it.
3. Quote the exact vulnerable lines with their line numbers.

Return exactly this markdown list and nothing else:

- **Vulnerability**: the name of the flaw
- **Location**: the file, the line numbers, and the exact lines
- **Exploit**: a step-by-step proof of concept, at most 150 words

Tone: clinical, technical, written for senior engineers. Give no remediation advice and no general security best practices.

If the input contains no analysable code (prose, configuration only, an empty file, or a function with no logic), respond with exactly:
INSUFFICIENT_CODE_FOR_ANALYSIS
```

## 3. Relay the result

Print the reviewer's output unchanged as the whole answer. Add no summary, verdict, severity rating or fix of your own. If the user then wants the flaw fixed, that is a separate request, made after this review.

The exploit is a theoretical walk-through. This command does not run it, and nothing here should be run against a live system.
