# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Public API for generating manifests."""

import logging
import json
from pathlib import Path

from manifest_builder import __version__
from manifest_builder.config import (
    load_configs,
    load_extra_variables,
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
    vars_from: Path | None = None,
    namespace: str | None = None,
    image: str | None = None,
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
        vars_from: Optional path to a TOML file of extra template variables,
            merged into the ``[variables]`` table from config.toml. Resolved
            relative to ``repo_root`` if it is not absolute.
        namespace: Optional namespace-owner mode. When set, config entries may
            omit their ``namespace`` field, an owner declaration is written to
            ``output/owners/<namespace>.toml``, and cluster-scoped output is
            rejected.
        image: Optional image override for namespace-owner mode. When set,
            simple and website config entries use this image and must not also
            set an ``image`` field in the config file.

    Returns:
        Set of manifest paths written during generation.
    """
    if repo_root is None:
        repo_root = Path.cwd()

    config = repo_root / config
    output = repo_root / output
    extra_variables = (
        load_extra_variables(repo_root / vars_from) if vars_from is not None else None
    )

    if image is not None and namespace is None:
        raise ValueError("generate(image=...) can only be used when namespace is set")

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
    handlers = load_configs(
        config,
        handlers,
        extra_variables=extra_variables,
        default_namespace=namespace,
        default_image=image if namespace is not None else None,
    )
    handlers = resolve_configs(handlers, helmfile_data)

    if verbose:
        count = sum(1 for handler in handlers for _ in handler.iter_configs())
        logger.info("Loaded %d app configuration%s", count, plural(count))

    images = load_images(config)
    owned_namespaces = load_owned_namespaces(config) | load_owned_namespaces(output)
    if namespace is not None:
        owned_namespaces.discard(namespace)
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
        managed_namespaces={namespace} if namespace is not None else None,
    )

    if namespace is not None:
        cluster_paths = _cluster_output_paths(output, written_paths)
        if cluster_paths:
            details = "\n  ".join(str(path) for path in cluster_paths)
            raise ValueError(
                "--namespace mode cannot generate cluster-scoped manifests:\n  "
                f"{details}"
            )
        owner_path = _write_namespace_owner(output, namespace)
        written_paths.add(owner_path)

    if create_commit:
        config_commit = get_git_commit(config)
        commit_owned_namespaces = owned_namespaces
        if namespace is not None:
            commit_owned_namespaces = owned_namespaces | _non_target_output_owners(
                output, namespace
            )
        create_manifest_commit(
            output,
            __version__,
            config_commit,
            written_paths,
            commit_owned_namespaces,
        )

    return written_paths


def _cluster_output_paths(output: Path, paths: set[Path]) -> list[Path]:
    """Return generated paths that landed in the output cluster directory."""
    cluster_paths: list[Path] = []
    for path in paths:
        try:
            parts = path.relative_to(output).parts
        except ValueError:
            continue
        if len(parts) > 1 and parts[0] == "cluster":
            cluster_paths.append(path)
    return sorted(cluster_paths)


def _write_namespace_owner(output: Path, namespace: str) -> Path:
    """Write this builder's namespace owner declaration."""
    owner_dir = output / "owners"
    owner_dir.mkdir(parents=True, exist_ok=True)
    owner_path = owner_dir / f"{namespace}.toml"
    owner_path.write_text(f"namespace = {json.dumps(namespace)}\n")
    return owner_path


def _non_target_output_owners(output: Path, namespace: str) -> set[str]:
    """Return top-level output directories that namespace mode must not clean."""
    if not output.is_dir():
        return set()
    return {
        path.name
        for path in output.iterdir()
        if path.is_dir() and path.name not in {namespace, "owners"}
    }
