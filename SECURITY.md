# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main`  | ✅ Yes     |

Only the current `main` branch receives security fixes.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues privately by emailing **coding.projects.1642@proton.me**.

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested mitigations (optional)

You will receive an acknowledgment within 72 hours. We aim to release a fix within 14 days of a confirmed report, depending on severity and complexity.

## Scope

ContextLifecycle resolves session anchors, reads cognition state from `.context/`,
and runs hook decision logic. The primary security surface is:

- **Anchor path resolution** — `CL_ANCHOR` is trusted to point at a manifest repo;
  a malicious value could redirect where session state is read/written
- **YAML deserialization** — capsules, handoffs, and checkpoints are loaded with
  `yaml.safe_load`; only safe (non-constructor) YAML is parsed
- **Subprocess invocation** — `git status` is the only spawned process, run with a
  fixed argument list and a timeout (no shell interpolation)
- **Environment-derived state** — `CL_ANCHOR` / `CL_SESSION_ID` are read from the
  environment in the CLI layer only

## Out of Scope

- Vulnerabilities in `git` or other host binaries invoked by the library
- Caller-side trust of `CL_ANCHOR` / `CL_SESSION_ID` values
- Issues requiring physical access to the host machine
