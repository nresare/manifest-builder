# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Git utilities for manifest generation and versioning."""

import subprocess
from pathlib import Path


def get_git_commit(path: Path) -> str:
    """
    Get the current git commit hash of a directory.

    Args:
        path: Directory to get commit hash for

    Returns:
        Short commit hash (7 characters)

    Raises:
        RuntimeError: If not a git repository or git command fails
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
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


def create_manifest_commit(output_dir: Path, version: str, config_commit: str) -> None:
    """
    Create a git commit in the output directory.

    Removes all .yaml files (but keeps other files like README.md) and commits
    the current manifest state.

    Args:
        output_dir: Directory to create commit in
        version: Version of manifest-builder
        config_commit: Commit hash of the config directory

    Raises:
        RuntimeError: If git operations fail
    """
    try:
        # Remove all .yaml files from the output directory
        for yaml_file in output_dir.rglob("*.yaml"):
            yaml_file.unlink()

        # Stage all changes (added, modified, deleted)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=output_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Create commit with version and config info
        commit_message = (
            f"Generate manifests from config@{config_commit} "
            f"using manifest-builder v{version}"
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
