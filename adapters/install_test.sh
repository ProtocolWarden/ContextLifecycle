#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# Test harness for adapters/install.sh. Run: bash adapters/install_test.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
check() { if eval "$2"; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Pre-existing settings with an unrelated key — merge must preserve it.
mkdir -p "$TMP/.claude"
echo '{"enabledPlugins": {"x": true}}' > "$TMP/.claude/settings.json"

bash "$HERE/install.sh" "$TMP" --cli claude,codex >/dev/null 2>&1

check "pre_tool_use.sh installed"        "[[ -x '$TMP/.claude/hooks/pre_tool_use.sh' ]]"
check "stop.sh installed"                "[[ -x '$TMP/.claude/hooks/stop.sh' ]]"
check "hook carries CL_ANCHOR hard-require" "grep -q 'CL_ANCHOR is not set' '$TMP/.claude/hooks/pre_tool_use.sh'"
check "settings: enabledPlugins preserved" "python3 -c \"import json;import sys;sys.exit(0 if 'x' in json.load(open('$TMP/.claude/settings.json')).get('enabledPlugins',{}) else 1)\""
check "settings: PreToolUse wired"       "python3 -c \"import json;import sys;sys.exit(0 if 'PreToolUse' in json.load(open('$TMP/.claude/settings.json')).get('hooks',{}) else 1)\""

# Idempotent re-run must not error and must keep settings valid JSON.
bash "$HERE/install.sh" "$TMP" >/dev/null 2>&1
check "idempotent re-run keeps valid settings.json" "python3 -c \"import json;json.load(open('$TMP/.claude/settings.json'))\""

echo "── install.sh: ${PASS} passed, ${FAIL} failed ──"
[[ "$FAIL" -eq 0 ]]
