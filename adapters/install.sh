#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
#
# Tracked ContextLifecycle adapter installer.
#
# Wires CL's CLI adapters into a target repo's tool-config dirs. This is a
# COMMITTED, re-runnable setup script — the integration must not depend on
# .claude/settings.json (or codex/aider equivalents) being git-tracked, since
# those are frequently gitignored. Re-running re-syncs drifted copies back to
# the canonical adapter in this repo.
#
#   Usage: adapters/install.sh <repo-path> [--cli claude[,codex,aider]]
#
# Default --cli is "claude". codex and aider adapters are added in later phases;
# unknown/unbuilt CLIs are reported and skipped (never silently no-op).
set -euo pipefail

ADAPTERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET="${1:?usage: install.sh <repo-path> [--cli claude,codex,aider]}"
shift || true
CLIS="claude"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cli) CLIS="$2"; shift 2 ;;
    *) echo "install.sh: unknown arg: $1" >&2; exit 2 ;;
  esac
done

TARGET="$(cd "$TARGET" && pwd)"

_install_claude() {
  local src="${ADAPTERS_DIR}/claude"
  mkdir -p "${TARGET}/.claude/hooks"
  cp "${src}/hooks/pre_tool_use.sh" "${TARGET}/.claude/hooks/pre_tool_use.sh"
  cp "${src}/hooks/stop.sh"         "${TARGET}/.claude/hooks/stop.sh"
  chmod +x "${TARGET}/.claude/hooks/"*.sh
  # Merge the canonical hook wiring into settings.json, preserving other keys
  # (enabledPlugins, etc.). Idempotent: re-running overwrites only "hooks".
  python3 - "${TARGET}/.claude/settings.json" "${src}/settings.json" <<'PY'
import json, sys, pathlib
target, canonical = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
base = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
base["hooks"] = json.loads(canonical.read_text(encoding="utf-8"))["hooks"]
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
PY
  echo "  claude  → ${TARGET}/.claude/{hooks,settings.json}"
}

IFS=',' read -ra _clis <<< "$CLIS"
for cli in "${_clis[@]}"; do
  case "$cli" in
    claude) _install_claude ;;
    codex|aider)
      echo "  ${cli}   → adapter not built yet (phase 2/3); skipped" >&2 ;;
    *) echo "install.sh: unknown CLI '${cli}'" >&2; exit 2 ;;
  esac
done

echo "CL adapters installed into ${TARGET} (clis: ${CLIS})"
