"""Manifest generation orchestration."""

from pathlib import Path

from manifest_builder.config import ChartConfig, validate_config
from manifest_builder.helm import run_helm_template


def generate_manifests(
    configs: list[ChartConfig],
    output_dir: Path,
    repo_root: Path,
    verbose: bool = False,
) -> None:
    """
    Generate manifests for all configured charts.

    Args:
        configs: List of chart configurations
        output_dir: Directory to write generated manifests
        repo_root: Repository root for resolving relative paths
        verbose: If True, print detailed output

    Raises:
        ValueError: If configuration validation fails
        RuntimeError: If manifest generation fails
    """
    if not configs:
        print("No charts configured")
        return

    # Validate all configs first
    for config in configs:
        validate_config(config, repo_root)

    # Generate manifests
    success_count = 0
    for config in configs:
        if verbose:
            print(f"\nGenerating manifest for {config.name} ({config.namespace})...")
            print(f"  Chart: {config.chart}")
            if config.version:
                print(f"  Version: {config.version}")
            if config.values:
                print(f"  Values: {', '.join(config.values)}")

        # Resolve values file paths
        values_paths = [repo_root / v for v in config.values]

        # Generate manifest
        try:
            manifest_content = run_helm_template(
                release_name=config.name,
                chart=config.chart,
                namespace=config.namespace,
                values_files=values_paths,
                version=config.version,
            )

            # Write to output file
            output_path = output_dir / config.namespace / f"{config.name}.yaml"
            write_manifest(manifest_content, output_path)

            print(f"✓ {config.name} ({config.namespace}) -> {output_path}")
            success_count += 1

        except Exception as e:
            print(f"✗ {config.name} ({config.namespace}): {e}")
            raise

    print(f"\nDone! Generated {success_count} manifest(s)")


def write_manifest(content: str, output_path: Path) -> None:
    """
    Write manifest content to a file.

    Args:
        content: YAML manifest content
        output_path: Path to write the manifest

    Raises:
        OSError: If file cannot be written
    """
    # Create parent directories if they don't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the manifest
    with open(output_path, "w") as f:
        f.write(content)
