# ContextGuard — Claude Code Adapter

Implements the ContextGuard adapter contract using Claude Code hooks.

Claude Code hooks run shell scripts at lifecycle events. This adapter wires ContextGuard enforcement into PreToolUse and Stop hooks.

---

## Installation

```bash
# From your repo root
cp -r path/to/ContextLifecycle/adapters/claude/hooks/ .claude/hooks/
cp path/to/ContextLifecycle/adapters/claude/settings.json .claude/settings.json
```

Or merge `settings.json` into your existing `.claude/settings.json`.

Ensure the hook scripts are executable:

```bash
chmod +x .claude/hooks/*.sh
```

---

## Hook Mapping

| ContextGuard hook | Claude Code event | Script                    |
| ----------------- | ----------------- | ------------------------- |
| `pre_action`      | PreToolUse        | `hooks/pre_tool_use.sh`   |
| `pre_write`       | PreToolUse        | `hooks/pre_tool_use.sh`   |
| `pre_spawn`       | PreToolUse        | `hooks/pre_tool_use.sh`   |
| `on_stop`         | Stop              | `hooks/stop.sh`           |

All pre-action checks are handled in a single `pre_tool_use.sh` which branches on `tool_name`.

---

## Configuration

The hooks read `.context/config.yaml` from the repo root. If no config exists, defaults apply:

```yaml
guard:
  require_capsule: false
  enforce_lease: true
  capsule_path: ".context/active/"
  checkpoint_path: ".context/checkpoints/"
  handoff_path: ".context/handoffs/"
loop:
  checkpoint_on_stop: true
```

---

## Exit Codes

- `0` — allow
- `2` — block (Claude Code surfaces stderr to the operator)

Claude Code JSON output format is also supported:

```json
{"decision": "block", "reason": "message here"}
```

---

## Requirements

- `bash` 4+
- `python3` (for YAML parsing via `check_capsule.py`)
- `jq` (for JSON input parsing from Claude Code)

## Installing / re-syncing the adapter

The per-repo `.claude/hooks/` + `settings.json` wiring is installed by the
tracked, committed `adapters/install.sh` (NOT by hand — `.claude/settings.json`
is often gitignored, so the integration must be reproducible from a committed
script). Re-running re-syncs drifted copies to this canonical adapter:

```bash
adapters/install.sh /path/to/repo            # claude (default)
adapters/install.sh /path/to/repo --cli claude,codex,aider
```
