# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Command-line interface for manifest-builder."""

import sys
from pathlib import Path

import click

from manifest_builder._version import __version__
from manifest_builder.config import load_configs, resolve_configs
from manifest_builder.generator import generate_manifests, setup_logging
from manifest_builder.git_utils import (
    create_manifest_commit,
    get_git_commit,
    is_git_dirty,
)
from manifest_builder.helmfile import load_helmfile


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
    "--allow-dirty-config",
    is_flag=True,
    help="Allow creation of commit even if config directory has local changes",
)
def main(
    config_dir: Path,
    output_dir: Path,
    verbose: bool,
    create_commit: bool,
    allow_dirty_config: bool,
) -> None:
    """Generate Kubernetes manifests from Helm charts."""
    setup_logging(verbose=verbose)

    try:
        # Get the repository root (current working directory)
        repo_root = Path.cwd()

        # Resolve the paths relative to the repo root
        config_dir = repo_root / config_dir
        output_dir = repo_root / output_dir

        if verbose:
            click.echo(f"Repository root: {repo_root}")
            click.echo(f"Configuration directory: {config_dir}")
            click.echo(f"Output directory: {output_dir}")
            click.echo()

        # Load the helmfile if present
        helmfile_path = config_dir / "helmfile.yaml"
        helmfile_data = load_helmfile(helmfile_path) if helmfile_path.exists() else None
        if verbose and helmfile_data is not None:
            click.echo(
                f"Loaded helmfile.yaml: {len(helmfile_data.releases)} release(s)"
            )

        # Load and resolve the configurations
        configs = load_configs(config_dir)
        configs = resolve_configs(configs, helmfile_data)

        if verbose:
            click.echo(f"Loaded {len(configs)} chart configuration(s)")

        # Generate manifests
        written_paths = generate_manifests(
            configs=configs,
            output_dir=output_dir,
            repo_root=repo_root,
            verbose=verbose,
        )

        # Create commit if requested
        if create_commit:
            if is_git_dirty(config_dir) and not allow_dirty_config:
                raise ValueError(
                    "Config directory has local changes. Use --allow-dirty-config "
                    "to allow commit creation with uncommitted changes."
                )

            config_commit = get_git_commit(config_dir)
            create_manifest_commit(
                output_dir, __version__, config_commit, written_paths
            )
            click.echo(f"✓ Created commit in {output_dir}")

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
