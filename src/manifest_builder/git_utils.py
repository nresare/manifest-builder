# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Git utilities for manifest generation and versioning."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_DIFF_BYTES = 65536


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
        RuntimeError: If not a git repository or git command fails
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to get git commit for {path}: {e.stderr}") from e


def is_git_dirty(path: Path) -> bool:
    """
    Check if a git directory has uncommitted changes.

    Args:
        path: Directory to check

    Returns:
        True if there are uncommitted changes, False otherwise

    Raises:
        RuntimeError: If not a git repository or git command fails
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to check git status for {path}: {e.stderr}") from e


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


def get_manifest_diff(
    output_dir: Path,
    generated_files: set[Path],
    owned_namespaces: set[str] | None = None,
) -> str:
    """
    Return a context diff for the manifest changes that would be committed.

    Uses a temporary git index so added, modified, and deleted files are included
    without changing the output repository's real staging area. If the diff is
    larger than 65536 bytes, returns ``git diff --stat`` output instead.

    Args:
        output_dir: Directory to diff
        generated_files: Set of file paths that were generated in this run
        owned_namespaces: Namespaces owned by other services; their files are
            preserved during the pre-diff cleanup.

    Returns:
        Unified context diff output, or an empty string if there are no changes

    Raises:
        RuntimeError: If git operations fail
    """
    try:
        _prepare_manifest_changes(output_dir, generated_files, owned_namespaces)
        with tempfile.TemporaryDirectory(prefix="manifest-builder-index-") as temp_dir:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(temp_dir) / "index")}
            subprocess.run(
                ["git", "read-tree", "HEAD"],
                cwd=output_dir,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                ["git", "add", "-A"],
                cwd=output_dir,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            result = subprocess.run(
                ["git", "diff", "--cached"],
                cwd=output_dir,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            if len(result.stdout.encode()) > MAX_DIFF_BYTES:
                result = subprocess.run(
                    ["git", "diff", "--cached", "--stat"],
                    cwd=output_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            return result.stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to generate git diff in {output_dir}: {e.stderr}"
        ) from e


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
        # Stage all changes (added, modified, deleted)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=output_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Check if there are any changes to commit
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=output_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        if not status_result.stdout.strip():
            logger.info("There is nothing to commit.")
            return

        # Create commit with version and config info
        commit_message = (
            f"Generate manifests\n"
            f"\n"
            f"Config commit: {config_commit}\n"
            f"Tool version: {version}"
        )
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=output_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to create git commit in {output_dir}: {e.stderr}"
        ) from e
