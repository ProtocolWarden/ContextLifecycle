# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import time

from context_lifecycle.hooks.stop import StopReport, evaluate_stop
from context_lifecycle.models.config import CLConfig


def test_stop_no_checkpoint_with_enforcement(paths, default_config, tmp_path):
    marker = tmp_path / "marker"
    marker.touch()
    report = evaluate_stop(paths=paths, config=default_config, session_marker=marker)
    assert isinstance(report, StopReport)
    assert report.enforcement_message is not None
    assert "without a LoopCheckpoint" in report.enforcement_message


def test_stop_no_checkpoint_no_enforcement(paths, tmp_path):
    cfg = CLConfig.model_validate({"loop": {"checkpoint_on_stop": False}})
    marker = tmp_path / "marker"
    marker.touch()
    report = evaluate_stop(paths=paths, config=cfg, session_marker=marker)
    assert report.enforcement_message is None
    assert any("without a LoopCheckpoint" in w.reason for w in report.decision.warnings)


def test_stop_fresh_checkpoint_passes(paths, default_config, write_yaml_helper, tmp_path, valid_checkpoint_data):
    marker = tmp_path / "marker"
    marker.touch()
    time.sleep(0.05)
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    report = evaluate_stop(paths=paths, config=default_config, session_marker=marker)
    assert report.enforcement_message is None


def test_stop_stale_checkpoint_not_fresh(paths, default_config, write_yaml_helper, tmp_path, valid_checkpoint_data):
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    time.sleep(0.05)
    marker = tmp_path / "marker"
    marker.touch()
    report = evaluate_stop(paths=paths, config=default_config, session_marker=marker)
    assert report.enforcement_message is not None


def test_stop_no_marker_any_checkpoint_counts(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    report = evaluate_stop(paths=paths, config=default_config, session_marker=None)
    assert report.enforcement_message is None


def test_stop_active_capsule_status_warns(paths, default_config, write_yaml_helper, valid_capsule_data):
    write_yaml_helper(paths.active / "cap.yaml", valid_capsule_data)  # status=active
    # no fresh checkpoint, so we expect enforcement_message PLUS a warning
    report = evaluate_stop(paths=paths, config=default_config, session_marker=None)
    # capsule status warning
    assert any("status is still 'active'" in w.reason for w in report.decision.warnings)


def test_stop_resolved_capsule_no_warn(paths, default_config, write_yaml_helper, valid_capsule_data):
    valid_capsule_data["status"] = "resolved"
    write_yaml_helper(paths.active / "cap.yaml", valid_capsule_data)
    report = evaluate_stop(paths=paths, config=default_config, session_marker=None)
    assert not any("status is still 'active'" in w.reason for w in report.decision.warnings)
