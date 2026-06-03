#!/usr/bin/env bash
# ContextGuard — Claude Code PreToolUse adapter
# Implements: pre_action, pre_write, pre_spawn
#
# Receives JSON on stdin: {"tool_name": "...", "tool_input": {...}}
# Exit 0 = allow, exit 2 = block (stderr surfaced to operator)
# JSON output: {"decision": "block", "reason": "..."} also supported

set -euo pipefail

# --- Require a manifest anchor: cognition ALWAYS anchors at a manifest, never CWD ---
# CL_ANCHOR is exported by `cl session start <manifest>`. Without it there is no
# valid place for ContextGuard to read/write state, so block rather than fall
# back to the working directory (which produced orphaned, un-anchored .context).
if [[ -z "${CL_ANCHOR:-}" ]]; then
  echo '{"decision": "block", "reason": "ContextGuard: CL_ANCHOR is not set. Every session must be anchored at a manifest — run: cl session start <manifest>."}'
  exit 2
fi
REPO_ROOT="${CL_ANCHOR}"
CONFIG_FILE="${REPO_ROOT}/.context/config.yaml"

# --- Session marker (created on first tool call; stop.sh uses it to detect fresh checkpoints) ---
_SESSION_HASH="$(echo "$REPO_ROOT" | cksum | cut -d' ' -f1)"
SESSION_MARKER="/tmp/clp_session_${_SESSION_HASH}"
[[ -f "$SESSION_MARKER" ]] || touch "$SESSION_MARKER" 2>/dev/null || true

# --- Read hook input ---
INPUT="$(cat)"
# Prefer jq if available; fall back to python3 (already required for YAML parsing)
if command -v jq &>/dev/null; then
  TOOL_NAME="$(echo "$INPUT" | jq -r '.tool_name // ""')"
  TOOL_INPUT="$(echo "$INPUT" | jq -r '.tool_input // {}')"
else
  TOOL_NAME="$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || echo "")"
  TOOL_INPUT="$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('tool_input',{})))" 2>/dev/null || echo "{}")"
fi

# --- Load config (with defaults) ---
REQUIRE_CAPSULE=false
ENFORCE_LEASE=true
CAPSULE_PATH=".context/active/"
CHECKPOINT_PATH=".context/checkpoints/"
HANDOFF_PATH=".context/handoffs/"

if [[ -f "${CONFIG_FILE}" ]] && command -v python3 &>/dev/null; then
  REQUIRE_CAPSULE=$(python3 -c "
import sys
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(str(c.get('guard', {}).get('require_capsule', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")

  ENFORCE_LEASE=$(python3 -c "
import sys
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(str(c.get('guard', {}).get('enforce_lease', True)).lower())
except Exception:
    print('true')
" 2>/dev/null || echo "true")

  CAPSULE_PATH=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(c.get('guard', {}).get('capsule_path', '.context/active/'))
except Exception:
    print('.context/active/')
" 2>/dev/null || echo ".context/active/")

  HANDOFF_PATH=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(c.get('guard', {}).get('handoff_path', '.context/handoffs/'))
except Exception:
    print('.context/handoffs/')
" 2>/dev/null || echo ".context/handoffs/")
fi

# --- Helper: find active capsule ---
find_active_capsule() {
  local capsule_dir="${REPO_ROOT}/${CAPSULE_PATH}"
  if [[ -d "$capsule_dir" ]]; then
    find "$capsule_dir" -name "*.yaml" -not -name ".gitkeep" | head -1
  fi
}

# --- Helper: get field from YAML file ---
yaml_field() {
  local file="$1"
  local field="$2"
  python3 -c "
try:
    import yaml
    with open('${file}') as f:
        d = yaml.safe_load(f)
    val = d.get('${field}', '')
    print(val if val is not None else '')
except Exception:
    print('')
" 2>/dev/null || echo ""
}

# --- Helper: block with reason ---
block() {
  local reason="$1"
  echo "{\"decision\": \"block\", \"reason\": \"ContextGuard: ${reason}\"}"
  exit 2
}

# --- Helper: warn (non-blocking, writes to stderr) ---
warn() {
  echo "ContextGuard warning: $1" >&2
}

# --- Check: require_capsule (+ malformed YAML detection) ---
if [[ "$REQUIRE_CAPSULE" == "true" ]]; then
  ACTIVE_CAPSULE="$(find_active_capsule)"
  if [[ -z "$ACTIVE_CAPSULE" ]]; then
    block "No active capsule found in ${CAPSULE_PATH}. Create or load an InvestigationCapsule before proceeding."
  else
    # Validate capsule is parseable YAML with required identity fields
    CAPSULE_VALID=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_CAPSULE}') as f:
        d = yaml.safe_load(f)
    required = ['capsule_id', 'schema_version', 'status']
    missing = [k for k in required if not d.get(k)]
    print('ok' if not missing else 'missing:' + ','.join(missing))
except Exception as e:
    print('malformed:' + str(e)[:80])
" 2>/dev/null || echo "unreadable")
    if [[ "$CAPSULE_VALID" != "ok" ]]; then
      block "Active capsule is invalid (${CAPSULE_VALID}). Fix or remove ${ACTIVE_CAPSULE} before proceeding."
    fi
  fi
fi

# --- Check: lease expiry ---
if [[ "$ENFORCE_LEASE" == "true" ]]; then
  HANDOFF_DIR="${REPO_ROOT}/${HANDOFF_PATH}"
  if [[ -d "$HANDOFF_DIR" ]]; then
    ACTIVE_HANDOFF="$(find "$HANDOFF_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)"
    if [[ -n "$ACTIVE_HANDOFF" ]]; then
      EXPIRES_AT="$(yaml_field "$ACTIVE_HANDOFF" "expires_at")"
      if [[ -n "$EXPIRES_AT" ]]; then
        NOW_EPOCH=$(date -u +%s)
        EXPIRES_EPOCH=$(date -u -d "$EXPIRES_AT" +%s 2>/dev/null || date -u -jf "%Y-%m-%dT%H:%M:%SZ" "$EXPIRES_AT" +%s 2>/dev/null || echo "0")
        if [[ "$EXPIRES_EPOCH" -gt 0 && "$NOW_EPOCH" -gt "$EXPIRES_EPOCH" ]]; then
          block "Lease expired at ${EXPIRES_AT}. Write a LoopCheckpoint and escalate before continuing."
        fi
      fi
    fi
  fi
fi

# --- Check: pre_write — forbidden paths ---
if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
  if command -v jq &>/dev/null; then
    TARGET_PATH="$(echo "$TOOL_INPUT" | jq -r '.file_path // .path // ""')"
  else
    TARGET_PATH="$(echo "$TOOL_INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('file_path') or d.get('path') or '')" 2>/dev/null || echo "")"
  fi

  if [[ -n "$TARGET_PATH" ]]; then
    HANDOFF_DIR="${REPO_ROOT}/${HANDOFF_PATH}"
    if [[ -d "$HANDOFF_DIR" ]]; then
      ACTIVE_HANDOFF="$(find "$HANDOFF_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)"
      if [[ -n "$ACTIVE_HANDOFF" ]]; then
        FORBIDDEN=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    forbidden = d.get('worker_scope', {}).get('forbidden_paths', []) or []
    for p in forbidden:
        print(p)
except Exception:
    pass
" 2>/dev/null || true)

        while IFS= read -r forbidden_path; do
          if [[ -n "$forbidden_path" && "$TARGET_PATH" == "$forbidden_path"* ]]; then
            block "Path '${TARGET_PATH}' is forbidden by active worker scope (matches '${forbidden_path}')."
          fi
        done <<< "$FORBIDDEN"

        # Enforce allowed_paths whitelist — if non-empty, path must match at least one entry
        ALLOWED=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    allowed = d.get('worker_scope', {}).get('allowed_paths', []) or []
    for p in allowed:
        print(p)
except Exception:
    pass
" 2>/dev/null || true)

        if [[ -n "$ALLOWED" ]]; then
          PATH_ALLOWED=false
          while IFS= read -r allowed_path; do
            if [[ -n "$allowed_path" && "$TARGET_PATH" == "$allowed_path"* ]]; then
              PATH_ALLOWED=true
              break
            fi
          done <<< "$ALLOWED"
          if [[ "$PATH_ALLOWED" == "false" ]]; then
            block "Path '${TARGET_PATH}' is outside worker scope allowed_paths. Permitted prefixes: $(echo "$ALLOWED" | tr '\n' ' ')"
          fi
        fi

        MUTATION_POLICY=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    print(d.get('worker_scope', {}).get('mutation_policy', 'write_allowed'))
except Exception:
    print('write_allowed')
" 2>/dev/null || echo "write_allowed")

        if [[ "$MUTATION_POLICY" == "read_only" ]]; then
          block "Worker scope is read_only. Write operations are not permitted."
        fi
      fi
    fi
  fi
fi

# --- Check: pre_spawn — subagent budget ---
if [[ "$TOOL_NAME" == "Agent" ]]; then
  HANDOFF_DIR="${REPO_ROOT}/${HANDOFF_PATH}"
  if [[ -d "$HANDOFF_DIR" ]]; then
    ACTIVE_HANDOFF="$(find "$HANDOFF_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)"
    if [[ -n "$ACTIVE_HANDOFF" ]]; then
      MAX_SUBAGENTS=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_HANDOFF}') as f:
        d = yaml.safe_load(f)
    print(d.get('lease', {}).get('max_subagents', -1))
except Exception:
    print(-1)
" 2>/dev/null || echo "-1")

      if [[ "$MAX_SUBAGENTS" == "0" ]]; then
        block "Active lease prohibits subagent spawning (max_subagents: 0)."
      fi
    fi
  fi

  # Check context_risk.high_parallelism from latest checkpoint
  CHECKPOINT_DIR="${REPO_ROOT}/${CHECKPOINT_PATH}"
  if [[ -d "$CHECKPOINT_DIR" ]]; then
    LATEST_CHECKPOINT="$(find "$CHECKPOINT_DIR" -name "*.yaml" -not -name ".gitkeep" | sort | tail -1)"
    if [[ -n "$LATEST_CHECKPOINT" ]]; then
      HIGH_PARALLELISM=$(python3 -c "
try:
    import yaml
    with open('${LATEST_CHECKPOINT}') as f:
        d = yaml.safe_load(f)
    risk = d.get('orchestrator', {}).get('context_risk', {})
    print(str(risk.get('high_parallelism', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")

      if [[ "$HIGH_PARALLELISM" == "true" ]]; then
        block "context_risk.high_parallelism is true. Deny additional worker spawning until resolved."
      fi

      SUBAGENT_HEAVY=$(python3 -c "
try:
    import yaml
    with open('${LATEST_CHECKPOINT}') as f:
        d = yaml.safe_load(f)
    risk = d.get('orchestrator', {}).get('context_risk', {})
    print(str(risk.get('subagent_heavy', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")

      if [[ "$SUBAGENT_HEAVY" == "true" ]]; then
        warn "context_risk.subagent_heavy is true. Reduce subagent budget and avoid Explore escalation."
      fi
    fi
  fi
fi

# --- context_risk flags from latest checkpoint ---
CHECKPOINT_DIR="${REPO_ROOT}/${CHECKPOINT_PATH}"
if [[ -d "$CHECKPOINT_DIR" ]]; then
  LATEST_CHECKPOINT="$(find "$CHECKPOINT_DIR" -name "*.yaml" -not -name ".gitkeep" | sort | tail -1)"
  if [[ -n "$LATEST_CHECKPOINT" ]]; then
    _RISK=$(python3 -c "
try:
    import yaml
    with open('${LATEST_CHECKPOINT}') as f:
        d = yaml.safe_load(f)
    risk = d.get('orchestrator', {}).get('context_risk', {})
    import json; print(json.dumps(risk))
except Exception:
    print('{}')
" 2>/dev/null || echo "{}")

    # long_lived_session — warn: compact before continuing
    if echo "$_RISK" | python3 -c "import sys,json; r=json.load(sys.stdin); exit(0 if r.get('long_lived_session') else 1)" 2>/dev/null; then
      warn "context_risk.long_lived_session is true. Compact context before continuing."
    fi

    # checkpoint_stale — block: require refresh before dispatch
    if echo "$_RISK" | python3 -c "import sys,json; r=json.load(sys.stdin); exit(0 if r.get('checkpoint_stale') else 1)" 2>/dev/null; then
      block "context_risk.checkpoint_stale is true. Write a fresh LoopCheckpoint before dispatching."
    fi

    # reload_scope_too_large — warn on expensive read operations
    if [[ "$TOOL_NAME" == "Read" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "Glob" ]]; then
      if echo "$_RISK" | python3 -c "import sys,json; r=json.load(sys.stdin); exit(0 if r.get('reload_scope_too_large') else 1)" 2>/dev/null; then
        warn "context_risk.reload_scope_too_large is true. Prune warm/cold context before broad reads."
      fi
    fi
  fi
fi

# All checks passed
# --- Warm context injection (Phase 2-wire, spec §4) ------------------------
# Inert until .context/config.yaml injection.enabled: true. Emits warm leaf-doc
# conventions as PreToolUse additionalContext (parsed on exit 0 only). Never
# blocks: any failure is swallowed and falls through to the final `exit 0`.
if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
  INJECT_ENABLED=false
  if [[ -f "${CONFIG_FILE}" ]] && command -v python3 &>/dev/null; then
    INJECT_ENABLED=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f) or {}
    print(str(c.get('injection', {}).get('enabled', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")
  fi

  if [[ "$INJECT_ENABLED" == "true" ]]; then
    # Entire emission isolated: any failure is swallowed, never blocks the call.
    {
      ENGINE="${REPO_ROOT}/.context/.engine/route.py"
      # routes.yaml uses repo-relative globs; strip the anchor prefix if absolute.
      REL_PATH="${TARGET_PATH#${REPO_ROOT}/}"
      if [[ -n "$REL_PATH" && -f "$ENGINE" ]]; then
        CTX="$(python3 "$ENGINE" --target "$REL_PATH" --root "$REPO_ROOT" 2>/dev/null || true)"
        if [[ -n "$CTX" ]]; then
          CTX="$CTX" python3 -c "
import json, os
ctx = os.environ.get('CTX', '')
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'additionalContext': ctx,
    }
}))
" 2>/dev/null || true
        fi
      fi
    } || true
  fi
fi

# --- Campaign-close consolidation trigger (Phase 5, spec §2.3) -------------
# Fires the dry-run distill/prune plan when the campaign boundary changes.
# Gated on injection.enabled; runs at most once per boundary change via a cheap
# mtime throttle (only does work when task.md is newer than the last-seen
# marker); consolidate.py is DRY-RUN here (no --apply) — emits a plan to stderr,
# mutates nothing. Wrapped so any failure falls through to the final `exit 0`.
{
  TASK="${REPO_ROOT}/.console/task.md"
  SEEN_FILE="${SESSION_MARKER}.last-seen-campaign"
  if [[ -f "$TASK" ]] && { [[ ! -f "$SEEN_FILE" ]] || [[ "$TASK" -nt "$SEEN_FILE" ]]; }; then
    INJECT_ENABLED=false
    if [[ -f "${CONFIG_FILE}" ]] && command -v python3 &>/dev/null; then
      INJECT_ENABLED=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f) or {}
    print(str(c.get('injection', {}).get('enabled', False)).lower())
except Exception:
    print('false')
" 2>/dev/null || echo "false")
    fi

    if [[ "$INJECT_ENABLED" == "true" ]]; then
      CAMP="${REPO_ROOT}/.context/.engine/campaign.py"
      ENGINE="${REPO_ROOT}/.context/.engine/consolidate.py"
      LAST_SEEN_ID=""; LAST_SEEN_HASH=""
      if [[ -f "$SEEN_FILE" ]]; then
        LAST_SEEN_ID="$(sed -n '1p' "$SEEN_FILE" 2>/dev/null || true)"
        LAST_SEEN_HASH="$(sed -n '2p' "$SEEN_FILE" 2>/dev/null || true)"
      fi
      CHANGED=$(LAST_SEEN_ID="$LAST_SEEN_ID" LAST_SEEN_HASH="$LAST_SEEN_HASH" \
        CAMP="$CAMP" TASK="$TASK" python3 -c "
import os, sys, importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('cl_campaign', os.environ['CAMP'])
m = importlib.util.module_from_spec(spec)
sys.modules['cl_campaign'] = m  # @dataclass needs the module in sys.modules
spec.loader.exec_module(m)
task = Path(os.environ['TASK'])
changed = m.boundary_changed(task, os.environ.get('LAST_SEEN_ID') or None,
                             os.environ.get('LAST_SEEN_HASH') or None)
camp = m.parse_task(task)
cid = (camp.campaign_id if camp else '') or ''
h = m.objective_hash(camp.objective) if camp else ''
print(('true' if changed else 'false') + '\t' + cid + '\t' + h)
" 2>/dev/null || printf 'false\t\t')

      FIRE="$(printf '%s' "$CHANGED" | cut -f1)"
      NEW_ID="$(printf '%s' "$CHANGED" | cut -f2)"
      NEW_HASH="$(printf '%s' "$CHANGED" | cut -f3)"

      if [[ "$FIRE" == "true" ]]; then
        PLAN="$(python3 "$ENGINE" --root "$REPO_ROOT" 2>/dev/null || true)"
        if [[ -n "$PLAN" ]]; then
          echo "ContextGuard: campaign boundary changed — consolidation plan (dry-run):" >&2
          printf '%s\n' "$PLAN" >&2
          echo "  Review, then run: python3 ${ENGINE} --root ${REPO_ROOT} --apply" >&2
        fi
      fi
      # Record last-seen + reset the mtime gate so we don't re-check until
      # task.md changes again (fires at most once per boundary change).
      printf '%s\n%s\n' "$NEW_ID" "$NEW_HASH" > "$SEEN_FILE" 2>/dev/null || true
    else
      # Flag off: reset the mtime gate cheaply so we don't recompute each call.
      touch "$SEEN_FILE" 2>/dev/null || true
    fi
  fi
} || true

exit 0
