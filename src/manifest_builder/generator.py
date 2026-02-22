# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Manifest generation orchestration."""

from pathlib import Path

import yaml

from manifest_builder.config import ChartConfig, WebsiteConfig, validate_config
from manifest_builder.helm import pull_chart, run_helm_template


def _literal_str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    """Represent multi-line strings using literal block scalar (|-) syntax."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


# Register the custom representer for multi-line strings
yaml.add_representer(str, _literal_str_representer)

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


def _generate_helm_manifests(
    config: ChartConfig,
    output_dir: Path,
    charts_dir: Path,
    verbose: bool = False,
) -> set[Path]:
    """Generate manifests from a Helm chart.

    Args:
        config: Helm chart configuration
        output_dir: Directory to write generated manifests
        charts_dir: Directory for caching pulled charts
        verbose: If True, print detailed output

    Returns:
        Set of paths written
    """
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

    # Pull the chart from the repo if configured
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

    manifest_content = run_helm_template(
        release_name=config.name,
        chart=chart_path,
        namespace=config.namespace,
        values_files=values_paths,
    )
    return write_manifests(
        manifest_content, output_dir, config.namespace, verbose, config.name
    )


def generate_manifests(
    configs: list[ChartConfig | WebsiteConfig],
    output_dir: Path,
    repo_root: Path,
    charts_dir: Path | None = None,
    verbose: bool = False,
) -> None:
    """
    Generate manifests for all configured apps.

    Args:
        configs: List of app configurations
        output_dir: Directory to write generated manifests
        repo_root: Repository root for resolving relative paths
        charts_dir: Directory for caching pulled charts (default: repo_root/.charts)
        verbose: If True, print detailed output

    Raises:
        ValueError: If configuration validation fails
        RuntimeError: If manifest generation fails
    """
    from manifest_builder.website import generate_website

    if not configs:
        print("No charts configured")
        return

    if charts_dir is None:
        charts_dir = repo_root / ".charts"

    # Validate all of the configs first
    for config in configs:
        validate_config(config, repo_root)

    # Generate manifests
    # Map the output paths to the config name that generated them
    written_paths: dict[Path, str] = {}
    for config in configs:
        try:
            if isinstance(config, WebsiteConfig):
                if verbose:
                    print(
                        f"\nGenerating manifest for {config.name} ({config.namespace})..."
                    )
                paths = generate_website(config, output_dir, verbose)
            else:
                paths = _generate_helm_manifests(
                    config, output_dir, charts_dir, verbose
                )

            # Check for conflicts with the previously written files
            conflicts = {p: written_paths[p] for p in paths if p in written_paths}
            if conflicts:
                conflict_details = []
                for path, previous_config in sorted(conflicts.items()):
                    conflict_details.append(f"{path} (generated by {previous_config})")
                conflict_list = "\n  ".join(conflict_details)
                raise ValueError(
                    f"Configuration conflict: {config.name} generates files that are already "
                    f"generated by another config:\n  {conflict_list}"
                )

            # Record which config generated the files
            for path in paths:
                written_paths[path] = config.name

            print(f"✓ {config.name} ({config.namespace}) -> {len(paths)} file(s)")

        except Exception:
            print(f"✗ {config.name} ({config.namespace})")
            raise

    # Remove any stale files left over from the previous runs
    _cleanup_stale_files(output_dir, written_paths, verbose)

    total = len(written_paths)
    summary = f"\nDone! Generated {total} manifest(s)"
    removed = _count_removed_files(output_dir, written_paths)
    if removed:
        summary += f", removed {removed} stale file(s)"
    print(summary)


def _cleanup_stale_files(
    output_dir: Path, written_paths: dict[Path, str], verbose: bool = False
) -> None:
    """Remove stale files and empty directories from previous runs.

    Args:
        output_dir: Directory to clean
        written_paths: Set of paths that were written in this run
        verbose: If True, print each file removed
    """
    if not output_dir.exists():
        return

    for existing in output_dir.rglob("*.yaml"):
        if existing not in written_paths:
            existing.unlink()
            if verbose:
                print(f"  removed {existing.relative_to(output_dir)}")

    # Remove any empty directories
    for directory in sorted(output_dir.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def _count_removed_files(output_dir: Path, written_paths: dict[Path, str]) -> int:
    """Count the number of stale files that were removed.

    Args:
        output_dir: Directory to check
        written_paths: Set of paths that were written in this run

    Returns:
        Number of removed files
    """
    if not output_dir.exists():
        return 0

    removed = 0
    for existing in output_dir.rglob("*.yaml"):
        if existing not in written_paths:
            removed += 1
    return removed


def _make_k8s_name(name: str) -> str:
    """Convert a name to a Kubernetes-safe name by replacing periods with dashes.

    Kubernetes object names must conform to RFC 1035 label naming rules:
    - Must be 63 characters or less
    - Must begin with an alphanumeric character
    - Must end with an alphanumeric character
    - May contain only lowercase alphanumerics or hyphens

    This converts names like 'example.com' to 'example-com'.

    Args:
        name: The original name (e.g., a domain name)

    Returns:
        A Kubernetes-safe name with periods replaced by dashes

    Raises:
        ValueError: If the resulting name violates RFC 1035 label naming constraints
    """
    k8s_name = name.replace(".", "-").lower()

    # Validate against RFC 1035 label naming constraints
    if not k8s_name:
        raise ValueError(f"Name '{name}' results in an empty Kubernetes object name")

    if len(k8s_name) > 63:
        raise ValueError(
            f"Kubernetes name '{k8s_name}' exceeds 63 character limit ({len(k8s_name)} characters)"
        )

    if not k8s_name[0].isalnum():
        raise ValueError(
            f"Kubernetes name '{k8s_name}' must start with an alphanumeric character, "
            f"but starts with '{k8s_name[0]}'"
        )

    if not k8s_name[-1].isalnum():
        raise ValueError(
            f"Kubernetes name '{k8s_name}' must end with an alphanumeric character, "
            f"but ends with '{k8s_name[-1]}'"
        )

    # Verify that only valid characters are present (lowercase alphanumeric and hyphens)
    if not all(c.isalnum() or c == "-" for c in k8s_name):
        invalid_chars = set(c for c in k8s_name if not (c.isalnum() or c == "-"))
        raise ValueError(
            f"Kubernetes name '{k8s_name}' contains invalid characters: {invalid_chars}. "
            f"Only lowercase alphanumerics and hyphens are allowed."
        )

    return k8s_name


def _strip_helm_from_metadata(metadata: dict) -> None:
    for key in ("labels", "annotations"):
        if key in metadata:
            metadata[key] = {
                k: v
                for k, v in metadata[key].items()
                if not k.startswith("helm.sh/")
                and not (k == "app.kubernetes.io/managed-by" and v == "Helm")
            }
            if not metadata[key]:
                del metadata[key]


def strip_helm_metadata(doc: dict) -> dict:
    """Remove helm-specific labels and annotations from a Kubernetes manifest."""
    _strip_helm_from_metadata(doc.get("metadata") or {})
    template_metadata = (doc.get("spec") or {}).get("template", {}).get("metadata")
    if template_metadata:
        _strip_helm_from_metadata(template_metadata)
    return doc


def _write_documents(
    documents: list[dict],
    output_dir: Path,
    namespace: str,
    verbose: bool = False,
    app_name: str | None = None,
) -> set[Path]:
    written: set[Path] = set()
    for doc in documents:
        strip_helm_metadata(doc)
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
            if app_name:
                f.write(f"# Source: {app_name}\n")
            yaml.dump(doc, f, default_flow_style=False, sort_keys=False)

        if verbose:
            print(f"  → {subdir}/{filename}")

        written.add(output_path)

    return written


def write_manifests(
    content: str,
    output_dir: Path,
    namespace: str,
    verbose: bool = False,
    app_name: str | None = None,
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
        app_name: If provided, written as a comment at the top of each file

    Returns:
        Set of paths written

    Raises:
        OSError: If files cannot be written
    """
    documents = [doc for doc in yaml.safe_load_all(content) if doc]

    # Add namespace to namespaced resources that don't already have one
    for doc in documents:
        kind = doc.get("kind")
        if kind and kind not in CLUSTER_SCOPED_KINDS:
            if "namespace" not in doc.get("metadata", {}):
                doc.setdefault("metadata", {})["namespace"] = namespace

    return _write_documents(documents, output_dir, namespace, verbose, app_name)
