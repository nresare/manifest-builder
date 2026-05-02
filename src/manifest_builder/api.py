# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Public API for generating manifests."""

import logging
from pathlib import Path

from manifest_builder import __version__
from manifest_builder.config import (
    load_configs,
    load_images,
    load_owned_namespaces,
    resolve_configs,
)
from manifest_builder.copy import CopyConfigHandler
from manifest_builder.generator import HelmConfigHandler, generate_manifests, plural
from manifest_builder.git_utils import (
    create_manifest_commit,
    get_git_commit,
    is_git_checkout,
    is_git_dirty,
)
from manifest_builder.helmfile import load_helmfile
from manifest_builder.simple import SimpleConfigHandler
from manifest_builder.website import WebsiteConfigHandler

logger = logging.getLogger(__name__)


def generate(
    config: Path,
    output: Path,
    repo_root: Path | None = None,
    verbose: bool = False,
    create_commit: bool = False,
    allow_dirty_config: bool = False,
) -> set[Path]:
    """Generate manifests from ``config`` into ``output``.

    Args:
        config: Configuration directory path, resolved relative to ``repo_root`` if
            it is not absolute.
        output: Output directory path, resolved relative to ``repo_root`` if it is
            not absolute.
        repo_root: Repository root for resolving relative paths. Defaults to the
            current working directory.
        verbose: If True, emit additional progress logging.
        create_commit: If True, create a git commit in the output checkout.
        allow_dirty_config: If True, allow commit creation when the config
            checkout has local changes.

    Returns:
        Set of manifest paths written during generation.
    """
    if repo_root is None:
        repo_root = Path.cwd()

    config = repo_root / config
    output = repo_root / output

    if create_commit and not is_git_checkout(output):
        raise ValueError(
            f"It doesn't seem like {output} is a git checkout, "
            "a requirement to be able to generate a commit."
        )

    if create_commit and is_git_dirty(config) and not allow_dirty_config:
        raise ValueError(
            "Config directory has local changes. Use --allow-dirty-config "
            "to allow commit creation with uncommitted changes."
        )

    if verbose:
        logger.info("Repository root: %s", repo_root)
        logger.info("Configuration directory: %s", config)
        logger.info("Output directory: %s", output)

    helmfile_path = config / "releases.yaml"
    helmfile_data = load_helmfile(helmfile_path) if helmfile_path.exists() else None
    if verbose and helmfile_data is not None:
        count = len(helmfile_data.releases)
        logger.info("Loaded releases.yaml: %d release%s", count, plural(count))

    handlers = [
        HelmConfigHandler(),
        WebsiteConfigHandler(),
        SimpleConfigHandler(),
        CopyConfigHandler(),
    ]
    handlers = load_configs(config, handlers)
    handlers = resolve_configs(handlers, helmfile_data)

    if verbose:
        count = sum(1 for handler in handlers for _ in handler.iter_configs())
        logger.info("Loaded %d app configuration%s", count, plural(count))

    images = load_images(config)
    owned_namespaces = load_owned_namespaces(config)
    if verbose and owned_namespaces:
        count = len(owned_namespaces)
        logger.info(
            "Loaded %d owned namespace%s: %s",
            count,
            plural(count),
            ", ".join(sorted(owned_namespaces)),
        )

    written_paths = generate_manifests(
        handlers=handlers,
        output_dir=output,
        repo_root=repo_root,
        images=images,
        verbose=verbose,
        owned_namespaces=owned_namespaces,
    )

    if create_commit:
        config_commit = get_git_commit(config)
        create_manifest_commit(
            output,
            __version__,
            config_commit,
            written_paths,
            owned_namespaces,
        )

    return written_paths
