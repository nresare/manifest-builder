"""Command-line interface for manifest-builder."""

import shutil
import sys
from pathlib import Path

import click

from manifest_builder.config import load_configs
from manifest_builder.generator import generate_manifests


@click.command()
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
    "--charts",
    type=str,
    help="Comma-separated list of chart names to generate (default: all)",
)
@click.option(
    "--clean",
    is_flag=True,
    help="Remove output directory before generating",
)
def main(
    config_dir: Path,
    output_dir: Path,
    verbose: bool,
    charts: str | None,
    clean: bool,
) -> None:
    """Generate Kubernetes manifests from Helm charts."""
    try:
        # Get repository root (current working directory)
        repo_root = Path.cwd()

        # Resolve paths relative to repo root
        config_dir = repo_root / config_dir
        output_dir = repo_root / output_dir

        if verbose:
            click.echo(f"Repository root: {repo_root}")
            click.echo(f"Configuration directory: {config_dir}")
            click.echo(f"Output directory: {output_dir}")
            click.echo()

        # Clean output directory if requested
        if clean and output_dir.exists():
            if verbose:
                click.echo(f"Removing {output_dir}...")
            shutil.rmtree(output_dir)

        # Load configurations
        configs = load_configs(config_dir)

        if verbose:
            click.echo(f"Loaded {len(configs)} chart configuration(s)")

        # Filter by chart names if specified
        if charts:
            chart_names = {name.strip() for name in charts.split(",")}
            configs = [c for c in configs if c.name in chart_names]

            if not configs:
                click.echo(f"No charts found matching: {charts}", err=True)
                sys.exit(1)

            if verbose:
                click.echo(
                    f"Filtered to {len(configs)} chart(s): {', '.join(chart_names)}"
                )

        # Generate manifests
        generate_manifests(
            configs=configs,
            output_dir=output_dir,
            repo_root=repo_root,
            verbose=verbose,
        )

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
