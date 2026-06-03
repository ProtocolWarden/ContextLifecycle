#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
Validates all CLP example/template YAML files against required field contracts.
Exit 0 = all pass. Exit 1 = failures.
"""
import sys
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent

PASS = 0
FAIL = 0

CAPSULE_REQUIRED = [
    "capsule_id", "schema_version", "created_at", "updated_at",
    "created_by", "related_checkpoint_id", "status",
]

CHECKPOINT_REQUIRED = [
    "checkpoint_id", "schema_version", "created_at",
]

HANDOFF_REQUIRED = [
    "handoff_id", "created_at", "source_checkpoint_id",
    "source_capsule_id", "target_worker_id",
]


def check(label, path, required_fields, keys_only=False):
    """keys_only=True: just check keys exist (for templates with empty placeholders)"""
    global PASS, FAIL
    try:
        with open(path) as f:
            d = yaml.safe_load(f)
        if not isinstance(d, dict):
            print(f"  FAIL  {label}: not a YAML mapping")
            FAIL += 1
            return
        if keys_only:
            missing = [k for k in required_fields if k not in d]
        else:
            missing = [k for k in required_fields if not d.get(k)]
        if missing:
            print(f"  FAIL  {label}: missing {'keys' if keys_only else 'fields'}: {', '.join(missing)}")
            FAIL += 1
        else:
            print(f"  PASS  {label}")
            PASS += 1
    except Exception as e:
        print(f"  FAIL  {label}: parse error: {e}")
        FAIL += 1


def check_config(label, path):
    global PASS, FAIL
    try:
        with open(path) as f:
            d = yaml.safe_load(f)
        if not isinstance(d, dict):
            print(f"  FAIL  {label}: not a YAML mapping")
            FAIL += 1
            return
        required_sections = ["guard", "loop"]
        missing = [k for k in required_sections if k not in d]
        if missing:
            print(f"  FAIL  {label}: missing sections: {', '.join(missing)}")
            FAIL += 1
        else:
            print(f"  PASS  {label}")
            PASS += 1
    except Exception as e:
        print(f"  FAIL  {label}: parse error: {e}")
        FAIL += 1


print("\nCLP schema/example validation")
print("─" * 60)
print("\nInvestigationCapsule examples:")
check("pc_audit_capsule", REPO_ROOT / ".context/examples/pc_audit_capsule.yaml", CAPSULE_REQUIRED)
check("capsule_template", REPO_ROOT / ".context/templates/investigation_capsule.template.yaml",
      ["capsule_id", "schema_version", "status"], keys_only=True)

print("\nLoopCheckpoint examples:")
check("oc_watchdog_checkpoint", REPO_ROOT / ".context/examples/oc_watchdog_checkpoint.yaml", CHECKPOINT_REQUIRED)
check("checkpoint_template", REPO_ROOT / ".context/templates/loop_checkpoint.template.yaml",
      ["checkpoint_id", "schema_version"], keys_only=True)

print("\nWorkerHandoff:")
check("handoff_template", REPO_ROOT / ".context/templates/worker_handoff.template.yaml",
      HANDOFF_REQUIRED, keys_only=True)

print("\nConfig templates/presets:")
check_config("clp_config_template", REPO_ROOT / ".context/templates/clp_config.template.yaml")
check_config("preset_watchdog_loop", REPO_ROOT / "presets/watchdog-loop.yaml")
check_config("preset_audit_sitter", REPO_ROOT / "presets/audit-sitter.yaml")
check_config("preset_ci_investigator", REPO_ROOT / "presets/ci-investigator.yaml")

print(f"\n{'─' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    print("FAILED")
    sys.exit(1)
else:
    print("ALL PASS")
    sys.exit(0)
