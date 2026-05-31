# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for git utilities."""

import logging
from pathlib import Path
from typing import cast

from dulwich import porcelain
from dulwich.objects import Commit
from dulwich.repo import Repo
import pytest

from manifest_builder.git_utils import (
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


def test_create_manifest_commit_stages_full_output_by_default(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Commit creation stages the full output checkout when no scope is given."""
    porcelain.init(tmp_path)
    manifest = tmp_path / "surreal3" / "namespace-surreal3.yaml"
    manifest.parent.mkdir()
    manifest.write_text("apiVersion: v1\nkind: Namespace\n")
    first_commit = _commit_all(tmp_path)

    manifest.write_text("apiVersion: v1\nkind: Namespace\nmetadata: {}\n")
    with caplog.at_level(logging.INFO, logger="manifest_builder.git_utils"):
        create_manifest_commit(
            output_dir=tmp_path,
            version="1.2.3",
            config_remote="https://example.com/config.git",
            config_commit="abc123",
            generated_files={manifest},
        )

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


def test_create_manifest_commit_stages_only_requested_paths(
    tmp_path: Path,
) -> None:
    """Scoped commits leave pre-existing deletions outside the scope unstaged."""
    porcelain.init(tmp_path)
    target = tmp_path / "team-a" / "deployment-app.yaml"
    protected = tmp_path / "cluster" / "clusterrole-system:metrics-server.yaml"
    target.parent.mkdir()
    protected.parent.mkdir()
    target.write_text("apiVersion: apps/v1\nkind: Deployment\n")
    protected.write_text(
        "apiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRole\n"
    )
    first_commit = _commit_all(tmp_path)

    protected.unlink()
    target.write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata: {}\n")

    create_manifest_commit(
        output_dir=tmp_path,
        version="1.2.3",
        config_remote="https://example.com/config.git",
        config_commit="abc123",
        generated_files={target},
        stage_paths={tmp_path / "team-a"},
    )

    with Repo.discover(tmp_path) as repo:
        assert repo.head() != first_commit
    assert porcelain.status(tmp_path).unstaged == [
        b"cluster/clusterrole-system:metrics-server.yaml"
    ]
