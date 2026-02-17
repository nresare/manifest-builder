"""Manifest generation orchestration."""

from pathlib import Path

import yaml

from manifest_builder.config import ChartConfig, validate_config
from manifest_builder.helm import pull_chart, run_helm_template

# Kubernetes resource kinds that are cluster-scoped (not namespaced)
CLUSTER_SCOPED_KINDS = {
    "APIService",
    "CertificateSigningRequest",
    "ClusterRole",
    "ClusterRoleBinding",
    "CSIDriver",
    "CSINode",
    "CustomResourceDefinition",
    "FlowSchema",
    "IngressClass",
    "Namespace",
    "Node",
    "PersistentVolume",
    "PriorityClass",
    "PriorityLevelConfiguration",
    "RuntimeClass",
    "StorageClass",
    "MutatingWebhookConfiguration",
    "ValidatingWebhookConfiguration",
    "VolumeAttachment",
}


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
    written_paths: set[Path] = set()
    for config in configs:
        if verbose:
            print(f"\nGenerating manifest for {config.name} ({config.namespace})...")
            print(f"  Chart: {config.chart}")
            if config.repo:
                print(f"  Repo: {config.repo}")
            if config.version:
                print(f"  Version: {config.version}")
            if config.values:
                print(f"  Values: {', '.join(str(v) for v in config.values)}")

        if config.chart is None:
            raise ValueError(
                f"Chart '{config.name}' has no resolved chart reference; "
                "ensure resolve_configs() was called before generate_manifests()"
            )

        values_paths = config.values

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
            paths = write_manifests(
                manifest_content, output_dir, config.namespace, verbose
            )
            written_paths.update(paths)

            print(f"✓ {config.name} ({config.namespace}) -> {len(paths)} file(s)")

        except Exception as e:
            print(f"✗ {config.name} ({config.namespace}): {e}")
            raise

    # Remove any stale files left over from previous runs
    removed = 0
    if output_dir.exists():
        for existing in output_dir.rglob("*.yaml"):
            if existing not in written_paths:
                existing.unlink()
                removed += 1
                if verbose:
                    print(f"  removed {existing.relative_to(output_dir)}")
        # Remove any empty directories
        for directory in sorted(output_dir.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    total = len(written_paths)
    summary = f"\nDone! Generated {total} manifest(s)"
    if removed:
        summary += f", removed {removed} stale file(s)"
    print(summary)


def write_manifests(
    content: str, output_dir: Path, namespace: str, verbose: bool = False
) -> set[Path]:
    """
    Split YAML content into individual documents and write each to a separate file.

    Files are named following the pattern: kind-name.yaml, written into
    output_dir/<namespace>/ for namespaced resources or output_dir/cluster/
    for cluster-scoped resources.

    Args:
        content: YAML manifest content with multiple documents
        output_dir: Base output directory
        namespace: Kubernetes namespace (used for namespaced resources)
        verbose: If True, print each file written

    Returns:
        Set of paths written

    Raises:
        OSError: If files cannot be written
    """
    documents = [doc for doc in yaml.safe_load_all(content) if doc]

    written: set[Path] = set()
    for doc in documents:
        kind = doc.get("kind")
        name = doc.get("metadata", {}).get("name")

        if not kind or not name:
            continue

        if kind in CLUSTER_SCOPED_KINDS:
            subdir = "cluster"
        else:
            subdir = doc.get("metadata", {}).get("namespace") or namespace
        dest_dir = output_dir / subdir
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{kind.lower()}-{name}.yaml"
        output_path = dest_dir / filename

        with open(output_path, "w") as f:
            yaml.dump(doc, f, default_flow_style=False, sort_keys=False)

        if verbose:
            print(f"  → {subdir}/{filename}")

        written.add(output_path)

    return written
