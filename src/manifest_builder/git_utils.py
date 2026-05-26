# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Git utilities for manifest generation and versioning."""

import logging
from pathlib import Path

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from dulwich.repo import Repo

logger = logging.getLogger(__name__)


def _remove_namespace_only_directories(
    output_dir: Path, owned_namespaces: set[str] | None = None
) -> None:
    """Remove namespace directories that only contain their auto-generated Namespace.

    This cleanup is intentionally limited to commit creation, where we can safely
    prune directories that no longer contain any workload manifests but still have
    a checked-in auto-generated ``namespace-<namespace>.yaml``.
    """
    if not output_dir.exists():
        return

    owned = owned_namespaces or set()
    for namespace_dir in sorted(output_dir.iterdir()):
        if (
            not namespace_dir.is_dir()
            or namespace_dir.name == "cluster"
            or namespace_dir.name == "kube-system"
            or namespace_dir.name in owned
        ):
            continue

        files = sorted(path for path in namespace_dir.iterdir() if path.is_file())
        expected_namespace_file = namespace_dir / f"namespace-{namespace_dir.name}.yaml"

        if files == [expected_namespace_file]:
            expected_namespace_file.unlink()
            namespace_dir.rmdir()
            logger.debug("Removed namespace-only directory %s", namespace_dir.name)


def get_git_commit(path: Path) -> str:
    """
    Get the current git commit hash of a directory.

    Args:
        path: Directory to get commit hash for

    Returns:
        Full commit hash (40 characters)

    Raises:
        RuntimeError: If not a git repository or git operations fail
    """
    try:
        repo = Repo.discover(path)
        with repo:
            return repo.head().decode("ascii")
    except Exception as e:
        raise RuntimeError(f"Failed to get git commit for {path}: {e}") from e


def is_git_checkout(path: Path) -> bool:
    """
    Check whether a path is inside a git checkout.

    Args:
        path: Directory to check

    Returns:
        True if the directory is in a git checkout, False otherwise
    """
    if not path.is_dir():
        return False

    try:
        repo = Repo.discover(path)
        repo.close()
        return True
    except NotGitRepository:
        return False


def is_git_dirty(path: Path) -> bool:
    """
    Check if a path inside a git checkout has uncommitted changes.

    Args:
        path: Directory to check

    Returns:
        True if there are uncommitted changes, False otherwise

    Raises:
        RuntimeError: If not a git repository or git operations fail
    """
    try:
        repo = Repo.discover(path)
        try:
            return not _status_is_clean(porcelain.status(repo))
        finally:
            repo.close()
    except Exception as e:
        raise RuntimeError(f"Failed to check git status for {path}: {e}") from e


def _status_is_clean(status: porcelain.GitStatus) -> bool:
    """Return whether a Dulwich status has no staged, unstaged, or untracked paths."""
    return (
        not status.untracked
        and not status.unstaged
        and all(not paths for paths in status.staged.values())
    )


def _path_owner(path: Path, output_dir: Path) -> str | None:
    """Return the top-level namespace directory for ``path`` under ``output_dir``."""
    try:
        rel_parts = path.relative_to(output_dir).parts
    except ValueError:
        return None
    return rel_parts[0] if rel_parts else None


def _prepare_manifest_changes(
    output_dir: Path,
    generated_files: set[Path],
    owned_namespaces: set[str] | None = None,
) -> None:
    """Apply pre-commit cleanup so git sees the exact manifest change set."""
    owned = owned_namespaces or set()

    # Find all .yaml files in the output directory, excluding owned namespaces
    all_yaml_files = {
        p
        for p in output_dir.rglob("*.yaml")
        if not owned or _path_owner(p, output_dir) not in owned
    }

    # Remove old .yaml files that were not generated in this run
    old_yaml_files = all_yaml_files - generated_files
    for old_file in old_yaml_files:
        old_file.unlink()

    _remove_namespace_only_directories(output_dir, owned)


def create_manifest_commit(
    output_dir: Path,
    version: str,
    config_commit: str,
    generated_files: set[Path],
    owned_namespaces: set[str] | None = None,
) -> None:
    """
    Create a git commit in the output directory.

    Removes old .yaml files that were not generated in this run and commits all changes.
    Keeps other files like README.md.

    Args:
        output_dir: Directory to create commit in
        version: Version of manifest-builder
        config_commit: Commit hash of the config directory
        generated_files: Set of file paths that were generated in this run
        owned_namespaces: Namespaces owned by other services; their files are
            preserved during the pre-commit cleanup.

    Raises:
        RuntimeError: If git operations fail
    """
    try:
        _prepare_manifest_changes(output_dir, generated_files, owned_namespaces)

        porcelain.add(output_dir)
        if _status_is_clean(porcelain.status(output_dir)):
            logger.info("There is nothing to commit.")
            return

        commit_message = (
            f"Generate manifests\n"
            f"\n"
            f"Config commit: {config_commit}\n"
            f"Tool version: {version}"
        )
        porcelain.commit(output_dir, message=commit_message.encode())
        logger.info("Created manifest commit in %s", output_dir)
    except Exception as e:
        raise RuntimeError(f"Failed to create git commit in {output_dir}: {e}") from e
