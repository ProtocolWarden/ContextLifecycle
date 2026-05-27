#!/usr/bin/env bash
# ContextGuard hook test harness
# Runs pre_tool_use.sh with fixture inputs and validates exit codes / output.
# Execute from the repo root: bash adapters/claude/hooks/tests/run_hook_tests.sh

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
# Hooks resolve .context under CL_ANCHOR (the manifest anchor); for the test
# fixtures that anchor is the repo root itself.
export CL_ANCHOR="${REPO_ROOT}"
HOOK="${REPO_ROOT}/adapters/claude/hooks/pre_tool_use.sh"
CONFIG_FILE="${REPO_ROOT}/.context/config.yaml"
ACTIVE_DIR="${REPO_ROOT}/.context/active"
CHECKPOINT_DIR="${REPO_ROOT}/.context/checkpoints"
HANDOFF_DIR="${REPO_ROOT}/.context/handoffs"
TMP_DIR="${REPO_ROOT}/.context/tmp/hook-tests-$$"

PASS=0
FAIL=0
ERRORS=()

# Save original config
ORIG_CONFIG=""
if [[ -f "${CONFIG_FILE}" ]]; then
  ORIG_CONFIG=$(cat "${CONFIG_FILE}")
fi

cleanup() {
  rm -rf "$TMP_DIR"
  rm -f "${ACTIVE_DIR}"/test-*.yaml
  rm -f "${CHECKPOINT_DIR}"/test-*.yaml
  rm -f "${HANDOFF_DIR}"/test-*.yaml
  # Restore original config
  if [[ -n "$ORIG_CONFIG" ]]; then
    echo "$ORIG_CONFIG" > "${CONFIG_FILE}"
  fi
}
trap cleanup EXIT

mkdir -p "$TMP_DIR"

# --- Helper: run hook, capture outputs ---
run_hook() {
  local input="$1"
  local exit_code
  echo "$input" | bash "$HOOK" > "$TMP_DIR/stdout_cap" 2>"$TMP_DIR/stderr_cap"
  exit_code=$?
  local stdout stderr
  stdout=$(cat "$TMP_DIR/stdout_cap")
  stderr=$(cat "$TMP_DIR/stderr_cap")
  echo "EXIT:${exit_code}|STDOUT:${stdout}|STDERR:${stderr}"
}

# --- Helper: assert ---
assert() {
  local case_id="$1"
  local description="$2"
  local result="$3"
  local expected_exit="$4"
  local expect_block="${5:-}"   # substring to find in stdout for block cases
  local expect_warn="${6:-}"    # substring to find in stderr for warn cases

  local actual_exit
  actual_exit=$(echo "$result" | grep -oP 'EXIT:\K[0-9]+')
  local actual_stdout
  actual_stdout=$(echo "$result" | sed 's/EXIT:[0-9]*|STDOUT:\(.*\)|STDERR:.*/\1/')
  local actual_stderr
  actual_stderr=$(echo "$result" | sed 's/EXIT:[0-9]*|STDOUT:.*|STDERR:\(.*\)/\1/')

  local ok=true

  if [[ "$actual_exit" != "$expected_exit" ]]; then
    ok=false
    ERRORS+=("${case_id}: exit code — expected ${expected_exit}, got ${actual_exit}")
  fi

  if [[ -n "$expect_block" && "$actual_stdout" != *"$expect_block"* ]]; then
    ok=false
    ERRORS+=("${case_id}: stdout missing '${expect_block}' — got: ${actual_stdout}")
  fi

  if [[ -n "$expect_warn" && "$actual_stderr" != *"$expect_warn"* ]]; then
    ok=false
    ERRORS+=("${case_id}: stderr missing '${expect_warn}' — got: ${actual_stderr}")
  fi

  if [[ "$ok" == "true" ]]; then
    echo "  PASS  ${case_id}: ${description}"
    ((PASS++)) || true
  else
    echo "  FAIL  ${case_id}: ${description}"
    for e in "${ERRORS[@]}"; do echo "        ↳ $e"; done
    ((FAIL++)) || true
    ERRORS=()
  fi
}

echo ""
echo "ContextGuard hook test harness"
echo "Hook: ${HOOK}"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "────────────────────────────────────────────────────────────────"

# ════════════════════════════════════════════════════════════════════
# SECTION 1: require_capsule
# ════════════════════════════════════════════════════════════════════
echo ""
echo "Section 1: require_capsule"

# Write a config that enables require_capsule
cat > "${REPO_ROOT}/.context/config.yaml" <<'YAML'
clp_version: "0.1"
repo: "test"
guard:
  require_capsule: true
  enforce_lease: true
  capsule_path: ".context/active/"
  checkpoint_path: ".context/checkpoints/"
  handoff_path: ".context/handoffs/"
loop:
  checkpoint_on_stop: true
YAML

# C-01: require_capsule=true, no active capsule → BLOCK
result=$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"src/foo.py"}}')
assert "C-01" "require_capsule=true, no capsule → BLOCK" "$result" "2" "No active capsule"

# C-02: require_capsule=true, valid capsule → ALLOW
cat > "${ACTIVE_DIR}/test-valid.yaml" <<'YAML'
capsule_id: "inv-test-001"
schema_version: "0.1"
status: "active"
created_at: "2026-05-21T00:00:00Z"
updated_at: "2026-05-21T00:00:00Z"
created_by: "test"
YAML
result=$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"src/foo.py"}}')
assert "C-02" "require_capsule=true, valid capsule → ALLOW" "$result" "0"
rm -f "${ACTIVE_DIR}/test-valid.yaml"

# C-03: require_capsule=true, capsule missing 'status' → BLOCK
cat > "${ACTIVE_DIR}/test-missing-status.yaml" <<'YAML'
capsule_id: "inv-test-002"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
YAML
result=$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"src/foo.py"}}')
assert "C-03" "require_capsule=true, missing 'status' field → BLOCK" "$result" "2" "invalid"
rm -f "${ACTIVE_DIR}/test-missing-status.yaml"

# C-04: require_capsule=true, malformed YAML → BLOCK
printf 'capsule_id: "test"\nstatus: [unclosed' > "${ACTIVE_DIR}/test-malformed.yaml"
result=$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"src/foo.py"}}')
assert "C-04" "require_capsule=true, malformed YAML → BLOCK" "$result" "2" "invalid"
rm -f "${ACTIVE_DIR}/test-malformed.yaml"

# Disable require_capsule for remaining tests
cat > "${REPO_ROOT}/.context/config.yaml" <<'YAML'
clp_version: "0.1"
repo: "test"
guard:
  require_capsule: false
  enforce_lease: true
  capsule_path: ".context/active/"
  checkpoint_path: ".context/checkpoints/"
  handoff_path: ".context/handoffs/"
loop:
  checkpoint_on_stop: true
YAML

# ════════════════════════════════════════════════════════════════════
# SECTION 2: Lease expiry
# ════════════════════════════════════════════════════════════════════
echo ""
echo "Section 2: Lease expiry"

# C-05: Expired lease → BLOCK
cat > "${HANDOFF_DIR}/test-expired.yaml" <<'YAML'
handoff_id: "handoff-test-expired"
schema_version: "0.1"
created_at: "2026-01-01T00:00:00Z"
expires_at: "2026-01-01T01:00:00Z"
source_checkpoint_id: "chk-001"
source_capsule_id: "inv-001"
target_worker_id: "worker-001"
worker_scope:
  repo: "test"
  allowed_paths: []
  mutation_policy: "write_allowed"
lease:
  max_minutes: 60
  max_tool_calls: 50
  max_subagents: 1
YAML
result=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"ls"}}')
assert "C-05" "expired lease → BLOCK" "$result" "2" "Lease expired"
rm -f "${HANDOFF_DIR}/test-expired.yaml"

# C-06: Future lease → ALLOW
cat > "${HANDOFF_DIR}/test-future.yaml" <<'YAML'
handoff_id: "handoff-test-future"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
expires_at: "2099-01-01T00:00:00Z"
source_checkpoint_id: "chk-001"
source_capsule_id: "inv-001"
target_worker_id: "worker-001"
worker_scope:
  repo: "test"
  allowed_paths: []
  mutation_policy: "write_allowed"
lease:
  max_minutes: 60
  max_tool_calls: 50
  max_subagents: 1
YAML
result=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"ls"}}')
assert "C-06" "future lease → ALLOW" "$result" "0"

# C-07: No handoff → ALLOW (lease check skipped)
rm -f "${HANDOFF_DIR}/test-future.yaml"
result=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"ls"}}')
assert "C-07" "no handoff → ALLOW" "$result" "0"

# ════════════════════════════════════════════════════════════════════
# SECTION 3: pre_write — forbidden_paths
# ════════════════════════════════════════════════════════════════════
echo ""
echo "Section 3: pre_write — forbidden_paths"

cat > "${HANDOFF_DIR}/test-scope.yaml" <<'YAML'
handoff_id: "handoff-test-scope"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
expires_at: "2099-01-01T00:00:00Z"
source_checkpoint_id: "chk-001"
source_capsule_id: "inv-001"
target_worker_id: "worker-001"
worker_scope:
  repo: "test"
  allowed_paths:
    - "src/"
    - ".context/"
  forbidden_paths:
    - ".context/tmp/"
  mutation_policy: "write_allowed"
lease:
  max_minutes: 60
  max_tool_calls: 50
  max_subagents: 1
YAML

# C-08: Write to forbidden path → BLOCK
result=$(run_hook '{"tool_name":"Write","tool_input":{"file_path":".context/tmp/scratch.txt"}}')
assert "C-08" "write to forbidden path → BLOCK" "$result" "2" "forbidden"

# C-09: Write to non-forbidden path (in allowed_paths) → ALLOW
result=$(run_hook '{"tool_name":"Write","tool_input":{"file_path":"src/module/foo.py"}}')
assert "C-09" "write inside allowed path (not forbidden) → ALLOW" "$result" "0"

# ════════════════════════════════════════════════════════════════════
# SECTION 4: pre_write — allowed_paths whitelist
# ════════════════════════════════════════════════════════════════════
echo ""
echo "Section 4: pre_write — allowed_paths whitelist"

# C-10: Write outside allowed_paths → BLOCK
result=$(run_hook '{"tool_name":"Edit","tool_input":{"file_path":"config/prod.yaml"}}')
assert "C-10" "write outside allowed_paths → BLOCK" "$result" "2" "outside worker scope"

# C-11: Write inside allowed_paths → ALLOW
result=$(run_hook '{"tool_name":"Write","tool_input":{"file_path":"src/module/bar.py"}}')
assert "C-11" "write inside allowed_paths → ALLOW" "$result" "0"

# C-12: allowed_paths empty → no whitelist check
rm -f "${HANDOFF_DIR}/test-scope.yaml"
cat > "${HANDOFF_DIR}/test-scope-empty.yaml" <<'YAML'
handoff_id: "handoff-test-scope-empty"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
expires_at: "2099-01-01T00:00:00Z"
source_checkpoint_id: "chk-001"
source_capsule_id: "inv-001"
target_worker_id: "worker-001"
worker_scope:
  repo: "test"
  allowed_paths: []
  forbidden_paths: []
  mutation_policy: "write_allowed"
lease:
  max_minutes: 60
  max_tool_calls: 50
  max_subagents: 1
YAML
result=$(run_hook '{"tool_name":"Write","tool_input":{"file_path":"anywhere/file.py"}}')
assert "C-12" "allowed_paths empty → ALLOW (no whitelist restriction)" "$result" "0"
rm -f "${HANDOFF_DIR}/test-scope-empty.yaml"

# ════════════════════════════════════════════════════════════════════
# SECTION 5: mutation_policy
# ════════════════════════════════════════════════════════════════════
echo ""
echo "Section 5: mutation_policy"

cat > "${HANDOFF_DIR}/test-readonly.yaml" <<'YAML'
handoff_id: "handoff-test-readonly"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
expires_at: "2099-01-01T00:00:00Z"
source_checkpoint_id: "chk-001"
source_capsule_id: "inv-001"
target_worker_id: "worker-001"
worker_scope:
  repo: "test"
  allowed_paths: []
  forbidden_paths: []
  mutation_policy: "read_only"
lease:
  max_minutes: 60
  max_tool_calls: 50
  max_subagents: 1
YAML

# C-13: read_only + Write → BLOCK
result=$(run_hook '{"tool_name":"Write","tool_input":{"file_path":"src/foo.py"}}')
assert "C-13" "mutation_policy=read_only, Write → BLOCK" "$result" "2" "read_only"

# C-14: read_only + Read → ALLOW (read not a mutation)
result=$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"src/foo.py"}}')
assert "C-14" "mutation_policy=read_only, Read → ALLOW" "$result" "0"
rm -f "${HANDOFF_DIR}/test-readonly.yaml"

# ════════════════════════════════════════════════════════════════════
# SECTION 6: pre_spawn — subagent budget
# ════════════════════════════════════════════════════════════════════
echo ""
echo "Section 6: pre_spawn — subagent budget"

cat > "${HANDOFF_DIR}/test-no-subagents.yaml" <<'YAML'
handoff_id: "handoff-test-no-subagents"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
expires_at: "2099-01-01T00:00:00Z"
source_checkpoint_id: "chk-001"
source_capsule_id: "inv-001"
target_worker_id: "worker-001"
worker_scope:
  repo: "test"
  allowed_paths: []
  mutation_policy: "write_allowed"
lease:
  max_minutes: 60
  max_tool_calls: 50
  max_subagents: 0
YAML

# C-15: max_subagents=0, Agent spawn → BLOCK
result=$(run_hook '{"tool_name":"Agent","tool_input":{"prompt":"investigate something"}}')
assert "C-15" "max_subagents=0 → BLOCK Agent spawn" "$result" "2" "prohibits subagent"
rm -f "${HANDOFF_DIR}/test-no-subagents.yaml"

# ════════════════════════════════════════════════════════════════════
# SECTION 7: context_risk flags (via checkpoint)
# ════════════════════════════════════════════════════════════════════
echo ""
echo "Section 7: context_risk flags"

# C-16: high_parallelism=true + Agent → BLOCK
cat > "${CHECKPOINT_DIR}/test-high-parallel.yaml" <<'YAML'
checkpoint_id: "chk-test-high-parallel"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
current_phase: "investigating"
orchestrator:
  context_risk:
    high_parallelism: true
    subagent_heavy: false
    checkpoint_stale: false
    long_lived_session: false
    reload_scope_too_large: false
YAML
result=$(run_hook '{"tool_name":"Agent","tool_input":{"prompt":"do something"}}')
assert "C-16" "context_risk.high_parallelism=true → BLOCK Agent" "$result" "2" "high_parallelism"
rm -f "${CHECKPOINT_DIR}/test-high-parallel.yaml"

# C-17: subagent_heavy=true + Agent → WARN (exit 0)
cat > "${CHECKPOINT_DIR}/test-subagent-heavy.yaml" <<'YAML'
checkpoint_id: "chk-test-subagent-heavy"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
current_phase: "investigating"
orchestrator:
  context_risk:
    high_parallelism: false
    subagent_heavy: true
    checkpoint_stale: false
    long_lived_session: false
    reload_scope_too_large: false
YAML
result=$(run_hook '{"tool_name":"Agent","tool_input":{"prompt":"do something"}}')
assert "C-17" "context_risk.subagent_heavy=true → WARN on Agent (exit 0)" "$result" "0" "" "subagent_heavy"
rm -f "${CHECKPOINT_DIR}/test-subagent-heavy.yaml"

# C-18: checkpoint_stale=true → BLOCK all tools
cat > "${CHECKPOINT_DIR}/test-stale.yaml" <<'YAML'
checkpoint_id: "chk-test-stale"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
current_phase: "investigating"
orchestrator:
  context_risk:
    high_parallelism: false
    subagent_heavy: false
    checkpoint_stale: true
    long_lived_session: false
    reload_scope_too_large: false
YAML
result=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"ls"}}')
assert "C-18" "context_risk.checkpoint_stale=true → BLOCK" "$result" "2" "checkpoint_stale"
rm -f "${CHECKPOINT_DIR}/test-stale.yaml"

# C-19: long_lived_session=true → WARN (exit 0)
cat > "${CHECKPOINT_DIR}/test-long-lived.yaml" <<'YAML'
checkpoint_id: "chk-test-long-lived"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
current_phase: "investigating"
orchestrator:
  context_risk:
    high_parallelism: false
    subagent_heavy: false
    checkpoint_stale: false
    long_lived_session: true
    reload_scope_too_large: false
YAML
result=$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"src/foo.py"}}')
assert "C-19" "context_risk.long_lived_session=true → WARN (exit 0)" "$result" "0" "" "long_lived_session"
rm -f "${CHECKPOINT_DIR}/test-long-lived.yaml"

# C-20: reload_scope_too_large=true + Read → WARN
cat > "${CHECKPOINT_DIR}/test-reload-large.yaml" <<'YAML'
checkpoint_id: "chk-test-reload-large"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
current_phase: "investigating"
orchestrator:
  context_risk:
    high_parallelism: false
    subagent_heavy: false
    checkpoint_stale: false
    long_lived_session: false
    reload_scope_too_large: true
YAML
result=$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"src/big.py"}}')
assert "C-20" "context_risk.reload_scope_too_large=true + Read → WARN" "$result" "0" "" "reload_scope_too_large"

# C-21: reload_scope_too_large=true + Write → no warn
result=$(run_hook '{"tool_name":"Write","tool_input":{"file_path":"src/foo.py"}}')
assert "C-21" "context_risk.reload_scope_too_large=true + Write → no warn" "$result" "0"
rm -f "${CHECKPOINT_DIR}/test-reload-large.yaml"

# C-22: all flags false → ALLOW with no warn
cat > "${CHECKPOINT_DIR}/test-all-false.yaml" <<'YAML'
checkpoint_id: "chk-test-all-false"
schema_version: "0.1"
created_at: "2026-05-21T00:00:00Z"
current_phase: "healthy"
orchestrator:
  context_risk:
    high_parallelism: false
    subagent_heavy: false
    checkpoint_stale: false
    long_lived_session: false
    reload_scope_too_large: false
YAML
result=$(run_hook '{"tool_name":"Bash","tool_input":{"command":"ls"}}')
assert "C-22" "all context_risk flags false → ALLOW, no warn" "$result" "0"
rm -f "${CHECKPOINT_DIR}/test-all-false.yaml"

# ── Anchor enforcement: no CL_ANCHOR → BLOCK (never fall back to CWD) ──
result=$(CL_ANCHOR="" bash "$HOOK" <<< '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
  > "$TMP_DIR/stdout_cap" 2>"$TMP_DIR/stderr_cap"; \
  echo "EXIT:$?|STDOUT:$(cat "$TMP_DIR/stdout_cap")|STDERR:$(cat "$TMP_DIR/stderr_cap")")
assert "C-23" "CL_ANCHOR unset → BLOCK (anchor required)" "$result" "2" "CL_ANCHOR is not set"

# ════════════════════════════════════════════════════════════════════
# RESULTS
# ════════════════════════════════════════════════════════════════════
echo ""
echo "────────────────────────────────────────────────────────────────"
echo "Results: ${PASS} passed, ${FAIL} failed"
if [[ "$FAIL" -gt 0 ]]; then
  echo "FAILED"
  exit 1
else
  echo "ALL PASS"
  exit 0
fi
