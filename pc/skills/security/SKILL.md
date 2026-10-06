---
name: security
description: Code security (DevSecOps) — keep secrets from leaking (keys, passwords, tokens) and leave no vulnerabilities (injections, unsafe deserialisation, weak cryptography). The scan_secrets and security_scan tools. For checking before a commit, a release, and before handing over code that works with data, the network or commands.
---

# Skill: code security

Two common troubles: a leaked secret and a hole in the code. Both are cheaper to catch before the
commit than to clean up after.

## Secrets — `scan_secrets`

Run it **before a commit and before publishing**: it finds private keys, API keys, tokens (AWS,
GitHub, Google, Slack, `sk-…`) and passwords hard-coded in the sources. What it finds is shown
masked.

Rules for secrets:
* A secret **never** lives in code and never goes into git. Secrets belong in environment
  variables / `.env`, and `.env` belongs in `.gitignore`.
* Found a secret in code — move it to the environment AND treat it as **compromised**: if it was
  already in a commit or published, it must be **revoked and reissued**, not just deleted from the
  line (git history remembers it).
* Never show the user or pass on the secret itself — only the fact of the finding and where it is.

## Vulnerabilities — `security_scan` (Bandit)

Run it for Python code that works with **data, the network, commands, serialisation**. Bandit
catches:
* command injection (`subprocess(..., shell=True)`, `os.system`);
* SQL injection (gluing strings into queries instead of parameters);
* unsafe deserialisation (`pickle.loads`, `yaml.load` without `safe_load`);
* `eval`/`exec` of user input;
* weak cryptography (md5/sha1 for passwords), hard-coded passwords.

Each finding has a severity (🔴 high / 🟠 medium / 🟡 low). Fix from high down: parameterise SQL,
`yaml.safe_load`, `subprocess` with an argument list and without `shell=True`,
`ast.literal_eval` instead of `eval`.

## When it is a must

* Before a commit — `scan_secrets`.
* Wrote code with a database, the network, commands or file uploads — `security_scan` before
  handing it over.
* Do not rely on "looks fine": injections and leaks are often invisible by eye, the tool sees them.
