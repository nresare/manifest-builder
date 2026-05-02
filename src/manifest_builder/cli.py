# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Command-line interface for manifest-builder."""

import logging
import sys
from pathlib import Path

import click

from manifest_builder import __version__
from manifest_builder.api import generate
from manifest_builder.generator import ManifestError, setup_logging
from manifest_builder.helm import get_helm_version

logger = logging.getLogger(__name__)


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
    """Generate Kubernetes manifests from configuration input."""
    setup_logging(verbose=verbose)

    try:
        # Log helm version
        helm_version = get_helm_version()
        logger.info(f"Using helm {helm_version}")
        generate(
            config_dir,
            output_dir,
            verbose=verbose,
            create_commit=create_commit,
            allow_dirty_config=allow_dirty_config,
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
