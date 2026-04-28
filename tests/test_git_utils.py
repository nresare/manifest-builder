# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for git commit cleanup utilities."""

import os
import subprocess
from pathlib import Path
from unittest import mock

from manifest_builder.git_utils import (
    _prepare_manifest_changes,
    _remove_namespace_only_directories,
    create_manifest_commit,
    get_manifest_diff,
)

# Isolate test git invocations from the user's global config (e.g. commit.gpgsign).
ISOLATED_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}


def test_remove_namespace_only_directories_prunes_orphaned_namespace(
    tmp_path: Path,
) -> None:
    """A namespace directory with only namespace-<name>.yaml is removed."""
    namespace_dir = tmp_path / "surreal3"
    namespace_dir.mkdir()
    namespace_manifest = namespace_dir / "namespace-surreal3.yaml"
    namespace_manifest.write_text("apiVersion: v1\nkind: Namespace\n")

    _remove_namespace_only_directories(tmp_path)

    assert not namespace_manifest.exists()
    assert not namespace_dir.exists()


def test_remove_namespace_only_directories_keeps_namespace_with_other_manifests(
    tmp_path: Path,
) -> None:
    """Namespace directories with workload manifests are preserved."""
    namespace_dir = tmp_path / "surreal3"
    namespace_dir.mkdir()
    namespace_manifest = namespace_dir / "namespace-surreal3.yaml"
    namespace_manifest.write_text("apiVersion: v1\nkind: Namespace\n")
    workload_manifest = namespace_dir / "deployment-app.yaml"
    workload_manifest.write_text("apiVersion: apps/v1\nkind: Deployment\n")

    _remove_namespace_only_directories(tmp_path)

    assert namespace_manifest.exists()
    assert workload_manifest.exists()
    assert namespace_dir.exists()


@mock.patch("manifest_builder.git_utils.subprocess.run")
def test_create_manifest_commit_prunes_namespace_only_directory_before_staging(
    mock_run: mock.Mock, tmp_path: Path
) -> None:
    """Commit creation removes namespace-only directories before git add -A."""
    namespace_dir = tmp_path / "surreal3"
    namespace_dir.mkdir()
    namespace_manifest = namespace_dir / "namespace-surreal3.yaml"
    namespace_manifest.write_text("apiVersion: v1\nkind: Namespace\n")

    mock_run.side_effect = [
        mock.Mock(stdout="", stderr=""),  # git add -A
        mock.Mock(stdout="D  surreal3/namespace-surreal3.yaml\n", stderr=""),
        mock.Mock(stdout="", stderr=""),  # git commit
    ]

    create_manifest_commit(
        output_dir=tmp_path,
        version="1.2.3",
        config_commit="abc123",
        generated_files={namespace_manifest},
    )

    assert not namespace_dir.exists()
    assert mock_run.call_args_list[0].args[0] == ["git", "add", "-A"]
    assert mock_run.call_args_list[1].args[0] == ["git", "status", "--porcelain"]
    assert mock_run.call_args_list[2].args[0] == ["git", "commit", "-m", mock.ANY]


@mock.patch("manifest_builder.git_utils.subprocess.run")
def test_get_manifest_diff_uses_temporary_index(
    mock_run: mock.Mock, tmp_path: Path
) -> None:
    """Diff generation stages manifest changes in a temporary index only."""
    manifest = tmp_path / "default" / "configmap-app.yaml"
    manifest.parent.mkdir()
    manifest.write_text("apiVersion: v1\nkind: ConfigMap\n")
    diff_output = (
        "diff --git a/default/configmap-app.yaml b/default/configmap-app.yaml\n"
    )

    mock_run.side_effect = [
        mock.Mock(stdout="", stderr=""),  # git read-tree HEAD
        mock.Mock(stdout="", stderr=""),  # git add -A
        mock.Mock(stdout=diff_output, stderr=""),  # git diff --cached
    ]

    result = get_manifest_diff(tmp_path, {manifest})

    assert result == diff_output
    assert mock_run.call_args_list[0].args[0] == ["git", "read-tree", "HEAD"]
    assert mock_run.call_args_list[1].args[0] == ["git", "add", "-A"]
    assert mock_run.call_args_list[2].args[0] == ["git", "diff", "--cached"]
    assert "GIT_INDEX_FILE" in mock_run.call_args_list[0].kwargs["env"]
    assert (
        mock_run.call_args_list[0].kwargs["env"]["GIT_INDEX_FILE"]
        == (mock_run.call_args_list[1].kwargs["env"]["GIT_INDEX_FILE"])
    )


@mock.patch("manifest_builder.git_utils.subprocess.run")
def test_get_manifest_diff_returns_stat_for_large_diff(
    mock_run: mock.Mock, tmp_path: Path
) -> None:
    """Diff generation falls back to --stat when full diff output is large."""
    manifest = tmp_path / "default" / "configmap-app.yaml"
    manifest.parent.mkdir()
    manifest.write_text("apiVersion: v1\nkind: ConfigMap\n")
    stat_output = " default/configmap-app.yaml | 70000 +++++++++++++++++++++++++\n"

    mock_run.side_effect = [
        mock.Mock(stdout="", stderr=""),  # git read-tree HEAD
        mock.Mock(stdout="", stderr=""),  # git add -A
        mock.Mock(stdout="x" * 65537, stderr=""),  # git diff --cached
        mock.Mock(stdout=stat_output, stderr=""),  # git diff --cached --stat
    ]

    result = get_manifest_diff(tmp_path, {manifest})

    assert result == stat_output
    assert mock_run.call_args_list[2].args[0] == ["git", "diff", "--cached"]
    assert mock_run.call_args_list[3].args[0] == [
        "git",
        "diff",
        "--cached",
        "--stat",
    ]


def test_get_manifest_diff_reports_untracked_files_without_staging_them(
    tmp_path: Path,
) -> None:
    """Diff mode includes new files while preserving the real git index."""
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=ISOLATED_GIT_ENV,
    )
    existing = tmp_path / "default" / "configmap-app.yaml"
    existing.parent.mkdir()
    existing.write_text("apiVersion: v1\nkind: ConfigMap\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, env=ISOLATED_GIT_ENV)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Manifest Builder",
            "-c",
            "user.email=manifest-builder@example.com",
            "commit",
            "-m",
            "Initial commit",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=ISOLATED_GIT_ENV,
    )

    existing.write_text("apiVersion: v1\nkind: ConfigMap\ndata: {}\n")
    new_manifest = tmp_path / "default" / "configmap-new.yaml"
    new_manifest.write_text("apiVersion: v1\nkind: ConfigMap\n")
    staged = tmp_path / "README.md"
    staged.write_text("Staged before diff mode\n")
    subprocess.run(
        ["git", "add", "README.md"], cwd=tmp_path, check=True, env=ISOLATED_GIT_ENV
    )

    diff = get_manifest_diff(tmp_path, {existing, new_manifest})
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert (
        "diff --git a/default/configmap-app.yaml b/default/configmap-app.yaml" in diff
    )
    assert (
        "diff --git a/default/configmap-new.yaml b/default/configmap-new.yaml" in diff
    )
    assert "A  README.md" in status
    assert "?? default/configmap-new.yaml" in status


def test_prepare_manifest_changes_preserves_owned_namespace_files(
    tmp_path: Path,
) -> None:
    """Pre-commit cleanup must leave files in owned namespace directories alone."""
    owned = tmp_path / "team-a" / "configmap-foo.yaml"
    owned.parent.mkdir(parents=True)
    owned.write_text("apiVersion: v1\nkind: ConfigMap\n")
    stale = tmp_path / "default" / "configmap-stale.yaml"
    stale.parent.mkdir()
    stale.write_text("apiVersion: v1\nkind: ConfigMap\n")
    fresh = tmp_path / "default" / "configmap-fresh.yaml"
    fresh.write_text("apiVersion: v1\nkind: ConfigMap\n")

    _prepare_manifest_changes(tmp_path, {fresh}, owned_namespaces={"team-a"})

    assert owned.exists()
    assert fresh.exists()
    assert not stale.exists()
