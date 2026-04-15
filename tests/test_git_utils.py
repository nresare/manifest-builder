# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for git commit cleanup utilities."""

from pathlib import Path
from unittest import mock

from manifest_builder.git_utils import (
    _remove_namespace_only_directories,
    create_manifest_commit,
)


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
