# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Git utilities for manifest generation and versioning."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from dulwich.objects import Blob, Commit, Tree
from dulwich.refs import Ref
from dulwich.repo import Repo

logger = logging.getLogger(__name__)


@dataclass
class GitManifestChanges:
    """Manifest file changes reported by git."""

    added: set[Path] = field(default_factory=set)
    modified: set[Path] = field(default_factory=set)
    deleted: set[Path] = field(default_factory=set)

    @property
    def added_or_modified(self) -> set[Path]:
        """Return files that exist in the working tree after generation."""
        return self.added | self.modified


class _GitConfig(Protocol):
    """Subset of the Dulwich config API used by remote resolution."""

    def sections(self) -> Iterable[tuple[bytes, ...]]: ...

    def get(self, section: tuple[bytes, ...], name: bytes) -> bytes: ...


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
            or namespace_dir.name.startswith(".")
            or namespace_dir.name in owned
        ):
            continue

        files = sorted(path for path in namespace_dir.iterdir() if path.is_file())
        expected_namespace_file = namespace_dir / f"namespace-{namespace_dir.name}.yaml"

        if files == [expected_namespace_file]:
            expected_namespace_file.unlink()
            logger.debug(
                "Deleted namespace-only manifest during commit cleanup: %s",
                expected_namespace_file.relative_to(output_dir),
            )
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


def get_git_tracked_remote(path: Path) -> str:
    """
    Get the URL of the remote that identifies the current checkout.

    Args:
        path: Directory to inspect

    Returns:
        URL of the upstream remote for the current branch, or a configured remote

    Raises:
        RuntimeError: If no remote can be resolved
    """
    try:
        repo = Repo.discover(path)
        with repo:
            head_ref = repo.refs.read_ref(cast(Ref, b"HEAD"))
            config = repo.get_config_stack()

            if head_ref is not None and head_ref.startswith(b"ref: refs/heads/"):
                branch_name = head_ref.removeprefix(b"ref: refs/heads/")
                try:
                    remote_name = config.get((b"branch", branch_name), b"remote")
                    remote_url = config.get((b"remote", remote_name), b"url")
                    return remote_url.decode("utf-8")
                except KeyError:
                    pass

            return _get_configured_remote_url(config)
    except Exception as e:
        raise RuntimeError(f"Failed to get git tracked remote for {path}: {e}") from e


def _get_configured_remote_url(config: _GitConfig) -> str:
    """Return the sole configured remote URL, or origin when several exist."""
    remote_names = sorted(
        section[1]
        for section in config.sections()
        if len(section) == 2 and section[0] == b"remote"
    )
    if not remote_names:
        raise RuntimeError("No git remotes are configured for the config checkout")

    remote_name = b"origin" if b"origin" in remote_names else remote_names[0]
    if len(remote_names) > 1 and remote_name != b"origin":
        names = ", ".join(
            name.decode("utf-8", errors="replace") for name in remote_names
        )
        raise RuntimeError(
            "Multiple git remotes are configured for the config checkout, "
            f"but none is named 'origin': {names}"
        )

    remote_url = config.get((b"remote", remote_name), b"url")
    return remote_url.decode("utf-8")


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


def get_git_manifest_changes(path: Path) -> GitManifestChanges:
    """Return changed YAML files below ``path`` using Dulwich status."""
    try:
        repo = Repo.discover(path)
        try:
            repo_root = Path(repo.path).resolve()
            output_root = path.resolve()
            status = porcelain.status(repo)
            changes = GitManifestChanges()

            for raw_path in status.staged.get("add", []):
                _add_status_path(changes.added, repo_root, output_root, raw_path)
            for raw_path in status.staged.get("modify", []):
                _add_status_path(changes.modified, repo_root, output_root, raw_path)
            for raw_path in status.staged.get("delete", []):
                _add_status_path(changes.deleted, repo_root, output_root, raw_path)

            for raw_path in status.untracked:
                _add_status_path(changes.added, repo_root, output_root, raw_path)

            for raw_path in status.unstaged:
                absolute_path = repo_root / raw_path.decode("utf-8")
                if absolute_path.exists():
                    _add_status_path(changes.modified, repo_root, output_root, raw_path)
                else:
                    _add_status_path(changes.deleted, repo_root, output_root, raw_path)

            return changes
        finally:
            repo.close()
    except Exception as e:
        raise RuntimeError(
            f"Failed to inspect git manifest changes in {path}: {e}"
        ) from e


def get_git_head_file(path: Path) -> bytes:
    """Return a file's contents from HEAD."""
    try:
        repo = Repo.discover(path)
        try:
            repo_root = Path(repo.path).resolve()
            relative_path = path.resolve().relative_to(repo_root)
            commit = cast(Commit, repo[repo.head()])
            tree = cast(Tree, repo[commit.tree])
            _mode, sha = tree.lookup_path(
                repo.object_store.__getitem__,
                str(relative_path).encode("utf-8"),
            )
            blob = cast(Blob, repo[sha])
            return blob.data
        finally:
            repo.close()
    except Exception as e:
        raise RuntimeError(f"Failed to read {path} from git HEAD: {e}") from e


def _add_status_path(
    paths: set[Path], repo_root: Path, output_root: Path, raw_path: bytes
) -> None:
    absolute_path = repo_root / raw_path.decode("utf-8")
    if absolute_path.suffix != ".yaml":
        return
    try:
        absolute_path.relative_to(output_root)
    except ValueError:
        return
    paths.add(absolute_path)


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
        if _should_manage_manifest_path(p, output_dir, owned)
    }

    # Remove old .yaml files that were not generated in this run
    old_yaml_files = all_yaml_files - generated_files
    for old_file in old_yaml_files:
        old_file.unlink()
        logger.debug(
            "Deleted stale manifest during commit cleanup: %s",
            old_file.relative_to(output_dir),
        )

    _remove_namespace_only_directories(output_dir, owned)


def create_manifest_commit(
    output_dir: Path,
    version: str,
    config_remote: str,
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
        config_remote: URL of the remote tracked by the config branch
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
            f"Config remote: {config_remote}\n"
            f"Config commit: {config_commit}\n"
            f"Tool version: {version}"
        )
        porcelain.commit(output_dir, message=commit_message.encode())
        logger.info("Created manifest commit in %s", output_dir)
    except Exception as e:
        raise RuntimeError(f"Failed to create git commit in {output_dir}: {e}") from e


def _should_manage_manifest_path(
    path: Path, output_dir: Path, owned_namespaces: set[str]
) -> bool:
    owner = _path_owner(path, output_dir)
    if owner is None or owner.startswith("."):
        return False
    return owner not in owned_namespaces
