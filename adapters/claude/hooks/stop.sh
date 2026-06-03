#!/usr/bin/env bash
# ContextGuard — Claude Code Stop adapter
# Implements: on_stop
#
# Runs when the Claude Code session ends.
# Warns if no checkpoint was written this session.
# Warns if active capsule was not updated.
# Blocks termination if loop.checkpoint_on_stop: true and no checkpoint written.

set -euo pipefail

# --- Manifest anchor: cognition ALWAYS anchors at a manifest, never CWD ---
# CL_ANCHOR is exported by `cl session start <manifest>`. On the Stop hook we
# skip gracefully when it is absent (no anchor → nothing to checkpoint here)
# rather than block the session from ending; enforcement lives in pre_tool_use.
if [[ -z "${CL_ANCHOR:-}" ]]; then
  echo "ContextGuard: CL_ANCHOR not set — no manifest anchor, skipping stop checks." >&2
  exit 0
fi
REPO_ROOT="${CL_ANCHOR}"
CONFIG_FILE="${REPO_ROOT}/.context/config.yaml"

# --- Session marker (written by pre_tool_use.sh on first tool call this session) ---
_SESSION_HASH="$(echo "$REPO_ROOT" | cksum | cut -d' ' -f1)"
SESSION_MARKER="/tmp/clp_session_${_SESSION_HASH}"

# --- Load config ---
CHECKPOINT_ON_STOP=true
CAPSULE_PATH=".context/active/"
CHECKPOINT_PATH=".context/checkpoints/"

if [[ -f "${CONFIG_FILE}" ]] && command -v python3 &>/dev/null; then
  CHECKPOINT_ON_STOP=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(str(c.get('loop', {}).get('checkpoint_on_stop', True)).lower())
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

  CHECKPOINT_PATH=$(python3 -c "
try:
    import yaml
    with open('${CONFIG_FILE}') as f:
        c = yaml.safe_load(f)
    print(c.get('guard', {}).get('checkpoint_path', '.context/checkpoints/'))
except Exception:
    print('.context/checkpoints/')
" 2>/dev/null || echo ".context/checkpoints/")
fi

# --- Helper: warn ---
warn() {
  echo "ContextGuard warning: $1" >&2
}

# --- Check: was a checkpoint written this session? ---
# Uses SESSION_MARKER (created by pre_tool_use.sh on first tool call) as the
# timestamp reference. find -newer detects only checkpoints written after session start.
# Falls back to existence check if the marker is absent (session without tool calls).

CHECKPOINT_DIR="${REPO_ROOT}/${CHECKPOINT_PATH}"
CHECKPOINT_FOUND=false

if [[ -d "$CHECKPOINT_DIR" ]]; then
  if [[ -f "$SESSION_MARKER" ]]; then
    SESSION_CHECKPOINT="$(find "$CHECKPOINT_DIR" -name "*.yaml" -not -name ".gitkeep" -newer "$SESSION_MARKER" | head -1)"
    if [[ -n "$SESSION_CHECKPOINT" ]]; then
      CHECKPOINT_FOUND=true
    fi
  else
    CHECKPOINT_COUNT=$(find "$CHECKPOINT_DIR" -name "*.yaml" -not -name ".gitkeep" | wc -l)
    if [[ "$CHECKPOINT_COUNT" -gt 0 ]]; then
      CHECKPOINT_FOUND=true
    fi
  fi
fi

if [[ "$CHECKPOINT_FOUND" == "false" ]]; then
  if [[ "$CHECKPOINT_ON_STOP" == "true" ]]; then
    echo "ContextGuard: Session ending without a LoopCheckpoint. Write a checkpoint before terminating." >&2
    echo "  Create: .context/checkpoints/<checkpoint-id>.yaml" >&2
    echo "  Template: .context/templates/loop_checkpoint.template.yaml" >&2
    # Non-fatal warn — Claude Code Stop hooks can't hard-block session end in all cases
    # but surfacing this prominently is the enforcement mechanism
  else
    warn "Session ending without a LoopCheckpoint."
  fi
fi

# --- Check: active capsule not updated ---
CAPSULE_DIR="${REPO_ROOT}/${CAPSULE_PATH}"
if [[ -d "$CAPSULE_DIR" ]]; then
  ACTIVE_CAPSULE=$(find "$CAPSULE_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)
  if [[ -n "$ACTIVE_CAPSULE" ]] && command -v python3 &>/dev/null; then
    CAPSULE_STATUS=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_CAPSULE}') as f:
        d = yaml.safe_load(f)
    print(d.get('status', 'active'))
except Exception:
    print('active')
" 2>/dev/null || echo "active")

    if [[ "$CAPSULE_STATUS" == "active" ]]; then
      warn "Active capsule '$(basename "$ACTIVE_CAPSULE")' status is still 'active'. Update status or handoff_notes before terminating."
    fi
  fi
fi

# --- Capture forcing function (Phase 3-capture, spec §2.3) -----------------
# Gated on injection.enabled; warn-only; wrapped so any failure falls through
# to the final `exit 0`. Reuses REPO_ROOT/CONFIG_FILE/CAPSULE_PATH from above.
{
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
    CAPSULE_DIR="${REPO_ROOT}/${CAPSULE_PATH}"
    if [[ -d "$CAPSULE_DIR" ]] && command -v python3 &>/dev/null; then
      ACTIVE_CAPSULE=$(find "$CAPSULE_DIR" -name "*.yaml" -not -name ".gitkeep" | head -1)
      if [[ -n "$ACTIVE_CAPSULE" ]]; then
        NEEDS_CAPTURE=$(python3 -c "
try:
    import yaml
    with open('${ACTIVE_CAPSULE}') as f:
        d = yaml.safe_load(f) or {}
    edited = bool(d.get('changed_files') or [])
    findings = d.get('findings') or []
    explicit_none = any(
        isinstance(x, str) and 'no durable findings' in x.lower()
        for x in (findings if isinstance(findings, list) else [])
    )
    print('true' if (edited and not findings and not explicit_none) else 'false')
except Exception:
    print('false')
" 2>/dev/null || echo "false")

        if [[ "$NEEDS_CAPTURE" == "true" ]]; then
          echo "ContextGuard: this session edited files but recorded no durable findings." >&2
          echo "  Add a one-line finding to the active capsule (findings:) or record" >&2
          echo "  \"no durable findings\" explicitly. See docs/architecture/context-injection-spec.md §2.3." >&2
        fi
      fi
    fi
  fi
} || true

exit 0
