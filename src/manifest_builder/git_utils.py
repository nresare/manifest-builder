# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Git utilities for manifest generation and versioning."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


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


def create_manifest_commit(
    output_dir: Path, version: str, config_commit: str, generated_files: set[Path]
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

    Raises:
        RuntimeError: If git operations fail
    """
    try:
        # Find all .yaml files in the output directory
        all_yaml_files = set(output_dir.rglob("*.yaml"))

        # Remove old .yaml files that were not generated in this run
        old_yaml_files = all_yaml_files - generated_files
        for old_file in old_yaml_files:
            old_file.unlink()

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
