# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Command-line interface for manifest-builder."""

import logging
import sys
from pathlib import Path

import click

from manifest_builder import __version__
from manifest_builder.config import (
    load_configs,
    load_images,
    load_owned_namespaces,
    resolve_configs,
)
from manifest_builder.copy import CopyConfigHandler
from manifest_builder.generator import (
    HelmConfigHandler,
    ManifestError,
    generate_manifests,
    plural,
    setup_logging,
)
from manifest_builder.git_utils import (
    create_manifest_commit,
    get_manifest_diff,
    get_git_commit,
    is_git_checkout,
    is_git_dirty,
)
from manifest_builder.helm import get_helm_version
from manifest_builder.helmfile import load_helmfile
from manifest_builder.simple import SimpleConfigHandler
from manifest_builder.website import WebsiteConfigHandler

logger = logging.getLogger(__name__)


def _show_diff(
    config: Path,
    output: Path,
    *,
    allow_dirty_config: bool = False,
) -> str:
    """Generate manifests and return the resulting git diff for the output repo."""
    if not is_git_checkout(output):
        raise ValueError(
            f"It doesn't seem like {output} is a git checkout, "
            "a requirement to be able to generate a diff."
        )

    if is_git_dirty(config) and not allow_dirty_config:
        raise ValueError(
            "Config directory has local changes. Use --allow-dirty-config "
            "to allow commit/diff creation with uncommitted changes."
        )

    helmfile_path = config / "releases.yaml"
    helmfile_data = load_helmfile(helmfile_path) if helmfile_path.exists() else None

    handlers = [HelmConfigHandler(), WebsiteConfigHandler(), CopyConfigHandler()]
    configs = load_configs(config, handlers)
    configs = resolve_configs(configs, helmfile_data)
    images = load_images(config)
    owned_namespaces = load_owned_namespaces(config)

    written_paths = generate_manifests(
        configs=configs,
        output_dir=output,
        repo_root=Path.cwd(),
        images=images,
        verbose=False,
        owned_namespaces=owned_namespaces,
    )

    return get_manifest_diff(output, written_paths, owned_namespaces)


def show_diff(config: Path, output: Path) -> str:
    """Generate manifests and return the resulting git diff for the output repo."""
    return _show_diff(config, output)


@click.command()
@click.version_option(version=__version__, prog_name="manifest-builder")
@click.option(
    "--config-dir",
    "-c",
    type=click.Path(exists=False, path_type=Path),
    default=Path("conf"),
    help="Configuration directory",
    show_default=True,
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(exists=False, path_type=Path),
    default=Path("output"),
    help="Output directory for generated manifests",
    show_default=True,
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed output",
)
@click.option(
    "--create-commit",
    is_flag=True,
    help="Create a git commit in the output directory with generated manifests",
)
@click.option(
    "--diff",
    "show_diff_requested",
    is_flag=True,
    help="Print a git diff of the generated manifest changes without committing",
)
@click.option(
    "--allow-dirty-config",
    is_flag=True,
    help="Allow creation of commit even if config directory has local changes",
)
def main(
    config_dir: Path,
    output_dir: Path,
    verbose: bool,
    create_commit: bool,
    show_diff_requested: bool,
    allow_dirty_config: bool,
) -> None:
    """Generate Kubernetes manifests from configuration input."""
    setup_logging(verbose=verbose)

    try:
        if create_commit and show_diff_requested:
            raise ValueError("Use only one of --create-commit or --diff.")

        # Log helm version
        helm_version = get_helm_version()
        logger.info(f"Using helm {helm_version}")

        # Get the repository root (current working directory)
        repo_root = Path.cwd()

        # Resolve the paths relative to the repo root
        config_dir = repo_root / config_dir
        output_dir = repo_root / output_dir

        if create_commit and not is_git_checkout(output_dir):
            raise ValueError(
                f"It doesn't seem like {output_dir} is a git checkout, "
                "a requirement to be able to generate a commit."
            )

        if verbose:
            click.echo(f"Repository root: {repo_root}")
            click.echo(f"Configuration directory: {config_dir}")
            click.echo(f"Output directory: {output_dir}")
            click.echo()

        if show_diff_requested:
            diff_output = _show_diff(
                config_dir,
                output_dir,
                allow_dirty_config=allow_dirty_config,
            )
            if diff_output:
                click.echo(diff_output, nl=False)
            else:
                click.echo("The output is identical before and after this change")
            return

        # Load the helmfile if present
        helmfile_path = config_dir / "releases.yaml"
        helmfile_data = load_helmfile(helmfile_path) if helmfile_path.exists() else None
        if verbose and helmfile_data is not None:
            count = len(helmfile_data.releases)
            click.echo(f"Loaded releases.yaml: {count} release{plural(count)}")

        # Load and resolve the configurations
        handlers = [
            HelmConfigHandler(),
            WebsiteConfigHandler(),
            SimpleConfigHandler(),
            CopyConfigHandler(),
        ]
        handlers = load_configs(config_dir, handlers)
        handlers = resolve_configs(handlers, helmfile_data)

        if verbose:
            count = sum(1 for handler in handlers for _ in handler.iter_configs())
            click.echo(f"Loaded {count} app configuration{plural(count)}")

        # Load container image definitions
        images = load_images(config_dir)

        # Load namespaces owned by other services or pipelines
        owned_namespaces = load_owned_namespaces(config_dir)
        if verbose and owned_namespaces:
            count = len(owned_namespaces)
            click.echo(
                f"Loaded {count} owned namespace{plural(count)}: "
                f"{', '.join(sorted(owned_namespaces))}"
            )

        # Fail fast before the time-consuming generation step
        if create_commit and is_git_dirty(config_dir) and not allow_dirty_config:
            raise ValueError(
                "Config directory has local changes. Use --allow-dirty-config "
                "to allow commit/diff creation with uncommitted changes."
            )

        # Generate manifests
        written_paths = generate_manifests(
            handlers=handlers,
            output_dir=output_dir,
            repo_root=repo_root,
            images=images,
            verbose=verbose,
            owned_namespaces=owned_namespaces,
        )

        if create_commit:
            config_commit = get_git_commit(config_dir)
            create_manifest_commit(
                output_dir,
                __version__,
                config_commit,
                written_paths,
                owned_namespaces,
            )

    except ManifestError as e:
        click.echo(f"Error processing {e.config_name}: {e}", err=True)
        sys.exit(1)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)
    except RuntimeError as e:
        click.echo(f"Runtime error: {e}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\nInterrupted by user", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
