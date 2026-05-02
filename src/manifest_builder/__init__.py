# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Manifest Builder - Generate Kubernetes manifests from configuration input."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = str(
        getattr(import_module("manifest_builder._version"), "__version__")
    )
except ModuleNotFoundError:
    try:
        __version__ = version("manifest-builder")
    except PackageNotFoundError:
        __version__ = "0.0.0"


def generate(
    config: Path,
    output: Path,
    repo_root: Path | None = None,
    verbose: bool = False,
    create_commit: bool = False,
    allow_dirty_config: bool = False,
) -> set[Path]:
    """Generate manifests from ``config`` into ``output``."""
    # Keep this wrapper lazy: api imports __version__ from this module.
    from manifest_builder.api import generate as api_generate

    return api_generate(
        config,
        output,
        repo_root,
        verbose,
        create_commit,
        allow_dirty_config,
    )


__all__ = ["__version__", "generate"]
