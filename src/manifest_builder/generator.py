"""Manifest generation orchestration."""

from pathlib import Path

import yaml

from manifest_builder.config import ChartConfig, validate_config
from manifest_builder.helm import pull_chart, run_helm_template


def generate_manifests(
    configs: list[ChartConfig],
    output_dir: Path,
    repo_root: Path,
    charts_dir: Path | None = None,
    verbose: bool = False,
) -> None:
    """
    Generate manifests for all configured charts.

    Args:
        configs: List of chart configurations
        output_dir: Directory to write generated manifests
        repo_root: Repository root for resolving relative paths
        charts_dir: Directory for caching pulled charts (default: repo_root/.charts)
        verbose: If True, print detailed output

    Raises:
        ValueError: If configuration validation fails
        RuntimeError: If manifest generation fails
    """
    if not configs:
        print("No charts configured")
        return

    if charts_dir is None:
        charts_dir = repo_root / ".charts"

    # Validate all configs first
    for config in configs:
        validate_config(config, repo_root)

    # Generate manifests
    success_count = 0
    for config in configs:
        if verbose:
            print(f"\nGenerating manifest for {config.name} ({config.namespace})...")
            print(f"  Chart: {config.chart}")
            if config.repo:
                print(f"  Repo: {config.repo}")
            if config.version:
                print(f"  Version: {config.version}")
            if config.values:
                print(f"  Values: {', '.join(config.values)}")

        # Resolve values file paths
        values_paths = [repo_root / v for v in config.values]

        # Pull chart from repo if configured
        if config.repo:
            version_suffix = f"-{config.version}" if config.version else ""
            pull_dest = charts_dir / f"{config.chart}{version_suffix}"
            if verbose:
                if (pull_dest / config.chart).exists():
                    print(f"  Using cached chart at {pull_dest / config.chart}")
                else:
                    print(f"  Pulling chart to {pull_dest / config.chart}")
            chart_path = str(
                pull_chart(config.chart, config.repo, pull_dest, config.version)
            )
        else:
            chart_path = config.chart

        # Generate manifest
        try:
            manifest_content = run_helm_template(
                release_name=config.name,
                chart=chart_path,
                namespace=config.namespace,
                values_files=values_paths,
            )

            # Split and write manifests to individual files
            output_namespace_dir = output_dir / config.namespace
            file_count = write_manifests(
                manifest_content, output_namespace_dir, verbose
            )

            print(
                f"✓ {config.name} ({config.namespace}) -> {file_count} file(s) in {output_namespace_dir}"
            )
            success_count += file_count

        except Exception as e:
            print(f"✗ {config.name} ({config.namespace}): {e}")
            raise

    print(f"\nDone! Generated {success_count} manifest(s)")


def write_manifests(content: str, output_dir: Path, verbose: bool = False) -> int:
    """
    Split YAML content into individual documents and write each to a separate file.

    Files are named following the pattern: kind-name.yaml

    Args:
        content: YAML manifest content with multiple documents
        output_dir: Directory to write the manifest files
        verbose: If True, print each file written

    Returns:
        Number of files written

    Raises:
        OSError: If files cannot be written
        ValueError: If YAML documents are missing required fields
    """
    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse all YAML documents
    documents = list(yaml.safe_load_all(content))

    # Filter out None/empty documents
    documents = [doc for doc in documents if doc]

    file_count = 0
    for doc in documents:
        # Extract kind and name
        kind = doc.get("kind")
        name = doc.get("metadata", {}).get("name")

        if not kind or not name:
            # Skip documents without kind or name (e.g., hooks, notes)
            continue

        # Create filename: kind-name.yaml (lowercase kind)
        filename = f"{kind.lower()}-{name}.yaml"
        output_path = output_dir / filename

        # Write the document
        with open(output_path, "w") as f:
            yaml.dump(doc, f, default_flow_style=False, sort_keys=False)

        if verbose:
            print(f"  → {filename}")

        file_count += 1

    return file_count
