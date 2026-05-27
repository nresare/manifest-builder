# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for git commit cleanup utilities."""

import logging
from pathlib import Path
from typing import cast

from dulwich import porcelain
from dulwich.objects import Commit
from dulwich.repo import Repo
import pytest

from manifest_builder.git_utils import (
    _prepare_manifest_changes,
    _remove_namespace_only_directories,
    create_manifest_commit,
    get_git_tracked_remote,
    is_git_checkout,
    is_git_dirty,
)


def _commit_all(path: Path, message: bytes = b"commit") -> bytes:
    """Commit all changes in a temporary Dulwich repository."""
    porcelain.add(path)
    return porcelain.commit(
        path,
        message=message,
        author=b"Test User <test@example.com>",
        committer=b"Test User <test@example.com>",
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


def test_is_git_checkout_returns_false_for_non_checkout(tmp_path: Path) -> None:
    """Non-git directories are not valid commit output targets."""
    assert not is_git_checkout(tmp_path)


def test_is_git_dirty_accepts_tracked_subdirectory(tmp_path: Path) -> None:
    """A clean tracked config subdirectory can be checked from inside a repo."""
    porcelain.init(tmp_path)
    config_dir = tmp_path / ".deploy"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text("name = 'example'\n")
    _commit_all(tmp_path)

    assert not is_git_dirty(config_dir)


def test_get_git_tracked_remote_returns_current_branch_remote_url(
    tmp_path: Path,
) -> None:
    """The tracked remote URL is resolved from the current branch config."""
    repo = porcelain.init(tmp_path)
    config = repo.get_config()
    config.set((b"remote", b"origin"), b"url", b"https://example.com/config.git")
    config.set((b"branch", b"master"), b"remote", b"origin")
    config.set((b"branch", b"master"), b"merge", b"refs/heads/main")
    config.write_to_path()
    repo.close()

    assert get_git_tracked_remote(tmp_path) == "https://example.com/config.git"


def test_get_git_tracked_remote_uses_only_remote_for_detached_head(
    tmp_path: Path,
) -> None:
    """Detached HEAD checkouts use the sole configured remote URL."""
    repo = porcelain.init(tmp_path)
    config = repo.get_config()
    config.set((b"remote", b"upstream"), b"url", b"https://example.com/config.git")
    config.write_to_path()
    repo.close()
    (tmp_path / "config.toml").write_text("name = 'example'\n")
    commit = _commit_all(tmp_path)
    porcelain.update_head(tmp_path, commit, detached=True)

    assert get_git_tracked_remote(tmp_path) == "https://example.com/config.git"


def test_get_git_tracked_remote_prefers_origin_for_detached_head(
    tmp_path: Path,
) -> None:
    """Detached HEAD checkouts with several remotes prefer origin."""
    repo = porcelain.init(tmp_path)
    config = repo.get_config()
    config.set((b"remote", b"fork"), b"url", b"https://example.com/fork.git")
    config.set((b"remote", b"origin"), b"url", b"https://example.com/config.git")
    config.write_to_path()
    repo.close()
    (tmp_path / "config.toml").write_text("name = 'example'\n")
    commit = _commit_all(tmp_path)
    porcelain.update_head(tmp_path, commit, detached=True)

    assert get_git_tracked_remote(tmp_path) == "https://example.com/config.git"


def test_get_git_tracked_remote_fails_when_no_remotes_are_configured(
    tmp_path: Path,
) -> None:
    """A checkout without configured remotes fails with an explicit error."""
    porcelain.init(tmp_path)

    with pytest.raises(RuntimeError, match="No git remotes are configured"):
        get_git_tracked_remote(tmp_path)


def test_create_manifest_commit_prunes_namespace_only_directory_before_staging(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Commit creation removes namespace-only directories before staging changes."""
    porcelain.init(tmp_path)
    namespace_dir = tmp_path / "surreal3"
    namespace_dir.mkdir()
    namespace_manifest = namespace_dir / "namespace-surreal3.yaml"
    namespace_manifest.write_text("apiVersion: v1\nkind: Namespace\n")
    first_commit = _commit_all(tmp_path)

    with caplog.at_level(logging.INFO, logger="manifest_builder.git_utils"):
        create_manifest_commit(
            output_dir=tmp_path,
            version="1.2.3",
            config_remote="https://example.com/config.git",
            config_commit="abc123",
            generated_files={namespace_manifest},
        )

    assert not namespace_dir.exists()
    assert porcelain.status(tmp_path).unstaged == []
    assert porcelain.status(tmp_path).untracked == []
    with Repo.discover(tmp_path) as repo:
        assert repo.head() != first_commit
        commit = cast(Commit, repo[repo.head()])
    assert commit.message == (
        b"Generate manifests\n"
        b"\n"
        b"Config remote: https://example.com/config.git\n"
        b"Config commit: abc123\n"
        b"Tool version: 1.2.3"
    )
    assert f"Created manifest commit in {tmp_path}" in caplog.messages


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
