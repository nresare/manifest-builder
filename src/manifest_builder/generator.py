# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Manifest generation orchestration."""

import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import pystache
import yaml
from pystache.common import MissingTags

from manifest_builder.config import (
    ChartConfig,
    SimpleConfig,
    TemplateValue,
    WebsiteConfig,
    validate_config,
)
from manifest_builder.helm import ChartCacheStats, pull_chart, run_helm_template

logger = logging.getLogger(__name__)

YAML_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
YAML_DUMPER: type[yaml.Dumper] = yaml.Dumper


class ManifestError(Exception):
    """Raised when manifest generation fails for a specific config."""

    def __init__(self, config_name: str, cause: Exception) -> None:
        self.config_name = config_name
        self.cause = cause
        super().__init__(f"{type(cause).__name__}: {cause}")


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with a formatter for console output.

    Args:
        verbose: If True, set log level to DEBUG; otherwise INFO
    """
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %z",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def _literal_str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    """Represent multi-line strings using literal block scalar (|-) syntax."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


# Register the custom representer for multi-line strings.
yaml.add_representer(str, _literal_str_representer, Dumper=YAML_DUMPER)


def _load_all_yaml(content: str) -> list[Any]:
    return [doc for doc in yaml.load_all(content, Loader=YAML_LOADER) if doc]


def _dump_yaml(doc: Any, stream: Any) -> None:
    yaml.dump(
        doc,
        stream,
        Dumper=YAML_DUMPER,
        default_flow_style=False,
        sort_keys=False,
    )


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
    images: dict[str, str] | None = None,
    cache_stats: ChartCacheStats | None = None,
) -> set[Path]:
    """Generate manifests from a Helm chart.

    Args:
        config: Helm chart configuration
        output_dir: Directory to write generated manifests
        charts_dir: Directory for caching pulled charts
        verbose: If True, log detailed output
        cache_stats: Optional chart cache hit/miss counter to update

    Returns:
        Set of paths written
    """
    logger.info(f"Generating manifest for {config.name} ({config.namespace})")
    logger.debug(f"Chart: {config.chart}")
    if config.repo:
        logger.debug(f"Repo: {config.repo}")
    if config.version:
        logger.debug(f"Version: {config.version}")
    if config.values:
        logger.debug(f"Values: {', '.join(str(v) for v in config.values)}")

    if config.chart is None:
        raise ValueError(
            f"Chart '{config.name}' has no resolved chart reference; "
            "ensure resolve_configs() was called before generate_manifests()"
        )

    # Pull the chart from the repo if configured (traditional or OCI)
    if config.repo or (config.chart and config.chart.startswith("oci://")):
        version_suffix = f"-{config.version}" if config.version else ""

        # Create a filesystem-safe directory name for the cache
        if config.chart.startswith("oci://"):
            chart_slug = config.chart.replace("oci://", "").replace("/", "_")
        else:
            chart_slug = config.chart

        pull_dest = charts_dir / f"{chart_slug}{version_suffix}"

        chart_path = str(
            pull_chart(
                chart=config.chart,
                dest=pull_dest,
                repo=config.repo,
                version=config.version,
                cache_stats=cache_stats,
            )
        )
    else:
        chart_path = config.chart

    values_context: dict[str, TemplateValue | str] = {
        **(images or {}),
        **config.variables,
    }
    with tempfile.TemporaryDirectory(prefix="manifest-builder-values-") as temp_dir:
        values_paths = _render_values_files(
            config.values, Path(temp_dir), values_context
        )
        manifest_content = run_helm_template(
            release_name=config.name,
            chart=chart_path,
            namespace=config.namespace,
            values_files=values_paths,
        )

    # Inject init container if configured
    if config.init:
        docs = _load_all_yaml(manifest_content)
        deployments = [d for d in docs if d.get("kind") == "Deployment"]
        if len(deployments) != 1:
            raise ValueError(
                f"init requires exactly one Deployment in chart '{config.name}', "
                f"found {len(deployments)}"
            )
        alpine_image = (images or {}).get("alpine_image")
        if not alpine_image:
            raise ValueError(
                f"init requires 'alpine_image' to be defined in images.toml "
                f"for '{config.name}'"
            )
        script = config.init.read_text()
        deployment = deployments[0]
        pod_spec = (
            deployment.setdefault("spec", {})
            .setdefault("template", {})
            .setdefault("spec", {})
        )
        # Collect unique volumeMounts from all containers
        seen = set()
        volume_mounts = []
        for container in pod_spec.get("containers", []):
            for vm in container.get("volumeMounts", []):
                key = (vm.get("name"), vm.get("mountPath"))
                if key not in seen:
                    seen.add(key)
                    volume_mounts.append(vm)
        init_container: dict = {
            "name": config.init.stem,
            "image": alpine_image,
            "command": ["/bin/sh", "-c", script],
        }
        if volume_mounts:
            init_container["volumeMounts"] = volume_mounts
        pod_spec["initContainers"] = [init_container]
        # Re-serialize; write_manifests will re-parse
        import io

        stream = io.StringIO()
        yaml.dump_all(
            docs,
            stream,
            Dumper=YAML_DUMPER,
            default_flow_style=False,
            sort_keys=False,
        )
        manifest_content = stream.getvalue()

    paths = write_manifests(manifest_content, output_dir, config.namespace, config.name)

    # Handle CRDs from the chart's crds directory (helm template doesn't include these)
    chart_dir_path = Path(chart_path)
    if chart_dir_path.is_dir():
        crds_dir = chart_dir_path / "crds"
        if crds_dir.is_dir():
            crd_docs: list[dict] = []
            start = time.perf_counter()
            for yaml_file in sorted(crds_dir.glob("**/*.yaml")):
                crd_docs.extend(_load_all_yaml(yaml_file.read_text()))
            if crd_docs:
                crd_paths = _write_documents(
                    crd_docs, output_dir, config.namespace, config.name
                )
                paths.update(crd_paths)
                elapsed = time.perf_counter() - start
                logger.info(f"Copied {len(crd_docs)} CRD(s) in {elapsed:.2f}s")

    # Handle extra resources if configured
    if config.extra_resources:
        renderer = pystache.Renderer(missing_tags=MissingTags.strict)
        extra_docs: list[dict] = []
        for yaml_file in sorted(config.extra_resources.glob("*.yaml")):
            rendered = renderer.render(yaml_file.read_text(), values_context)
            for doc in _load_all_yaml(rendered):
                # Add namespace to namespaced resources without one
                kind = doc.get("kind")
                if kind and kind not in CLUSTER_SCOPED_KINDS:
                    if "namespace" not in doc.get("metadata", {}):
                        doc.setdefault("metadata", {})["namespace"] = config.namespace
                extra_docs.append(doc)
        if extra_docs:
            extra_paths = _write_documents(
                extra_docs, output_dir, config.namespace, config.name
            )
            paths.update(extra_paths)
            logger.debug(f"Copied {len(extra_docs)} extra resources")

    return paths


def _render_values_files(
    values_paths: list[Path],
    temp_dir: Path,
    context: dict[str, TemplateValue | str],
) -> list[Path]:
    """Render values files as Mustache templates into temporary files."""
    if not values_paths:
        return []

    renderer = pystache.Renderer(missing_tags=MissingTags.strict)
    rendered_paths: list[Path] = []
    for index, values_path in enumerate(values_paths):
        rendered_path = temp_dir / f"{index:02d}-{values_path.name}"
        rendered_path.write_text(renderer.render(values_path.read_text(), context))
        rendered_paths.append(rendered_path)

    return rendered_paths


def generate_manifests(
    configs: list[ChartConfig | WebsiteConfig | SimpleConfig],
    output_dir: Path,
    repo_root: Path,
    images: dict[str, str] | None = None,
    charts_dir: Path | None = None,
    verbose: bool = False,
) -> set[Path]:
    """
    Generate manifests for all configured apps.

    Args:
        configs: List of app configurations
        output_dir: Directory to write generated manifests
        repo_root: Repository root for resolving relative paths
        images: Dict mapping image variable names to image references for template rendering
        charts_dir: Directory for caching pulled charts (default: repo_root/.charts)
        verbose: If True, log detailed output

    Returns:
        Set of paths that were written

    Raises:
        ValueError: If configuration validation fails
        RuntimeError: If manifest generation fails
    """
    from manifest_builder.website import generate_website

    if not configs:
        logger.info("No charts configured")
        return set()

    if charts_dir is None:
        charts_dir = Path.home() / ".cache" / "manifest-builder"

    # Validate all of the configs first
    for config in configs:
        validate_config(config, repo_root)

    # Generate manifests
    # Map the output paths to the config name that generated them
    written_paths: dict[Path, str] = {}
    cache_stats = ChartCacheStats()
    for config in configs:
        try:
            if isinstance(config, WebsiteConfig):
                logger.info(
                    f"Generating manifest for {config.name} ({config.namespace})"
                )
                paths = generate_website(
                    config, output_dir, images=images, verbose=verbose
                )
            elif isinstance(config, SimpleConfig):
                logger.info(
                    f"Generating manifest for {config.name} ({config.namespace})"
                )
                from manifest_builder.simple import generate_simple

                paths = generate_simple(config, output_dir, images=images)
            else:
                paths = _generate_helm_manifests(
                    config,
                    output_dir,
                    charts_dir,
                    verbose,
                    images=images,
                    cache_stats=cache_stats,
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

            logger.info(f"✓ {config.name} ({config.namespace}) -> {len(paths)} file(s)")

        except ManifestError:
            raise
        except Exception as e:
            logger.error(f"✗ {config.name} ({config.namespace})")
            raise ManifestError(config.name, e) from e

    # Create Namespace objects for any namespace that lacks one
    namespace_paths = _ensure_namespaces(output_dir, written_paths)
    written_paths.update(namespace_paths)

    # Remove any stale files left over from the previous runs
    _cleanup_stale_files(output_dir, written_paths)

    if cache_stats.hits or cache_stats.misses:
        logger.info(
            f"Chart cache: {cache_stats.hits} hit(s), {cache_stats.misses} miss(es)"
        )

    total = len(written_paths)
    summary = f"Done! Generated {total} manifest(s)"
    removed = _count_removed_files(output_dir, written_paths)
    if removed:
        summary += f", removed {removed} stale file(s)"
    logger.info(summary)

    return set(written_paths.keys())


def _ensure_namespaces(
    output_dir: Path, written_paths: dict[Path, str]
) -> dict[Path, str]:
    """Create Namespace objects for namespace directories that lack one.

    For each subdirectory of output_dir (excluding cluster/), checks whether a
    Namespace resource already exists for that namespace. If not, writes a minimal
    Namespace manifest to <output_dir>/<namespace>/namespace-<namespace>.yaml.

    Args:
        output_dir: Base output directory
        written_paths: Paths written so far in this run

    Returns:
        Dict of newly created namespace paths mapped to the source label
    """
    if not output_dir.exists():
        return {}

    new_paths: dict[Path, str] = {}
    for ns_dir in sorted(output_dir.iterdir()):
        if (
            not ns_dir.is_dir()
            or ns_dir.name == "cluster"
            or ns_dir.name == "kube-system"
        ):
            continue

        ns_name = ns_dir.name
        ns_filename = f"namespace-{ns_name}.yaml"

        # Skip if a Namespace was already written for this namespace
        if ns_dir / ns_filename in written_paths:
            continue
        if output_dir / "cluster" / ns_filename in written_paths:
            continue

        doc = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": ns_name},
        }
        out_path = ns_dir / ns_filename
        with open(out_path, "w") as f:
            _dump_yaml(doc, f)

        logger.debug(f"Created Namespace {ns_name}")
        new_paths[out_path] = "__namespaces__"

    return new_paths


def _cleanup_stale_files(output_dir: Path, written_paths: dict[Path, str]) -> None:
    """Remove stale files and empty directories from previous runs.

    Args:
        output_dir: Directory to clean
        written_paths: Set of paths that were written in this run
    """
    if not output_dir.exists():
        return

    for existing in output_dir.rglob("*.yaml"):
        if existing not in written_paths:
            existing.unlink()
            logger.debug(f"Removed {existing.relative_to(output_dir)}")

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
        if key in metadata and metadata[key] is not None:
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
    app_name: str | None = None,
) -> set[Path]:
    written: set[Path] = set()
    for doc in documents:
        kind = doc.get("kind", "unknown")
        name = doc.get("metadata", {}).get("name", "unknown")
        try:
            strip_helm_metadata(doc)
        except Exception as e:
            raise RuntimeError(
                f"Failed to strip helm metadata from {kind}/{name}: {e}"
            ) from e

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
            _dump_yaml(doc, f)

        logger.debug(f"Wrote {subdir}/{filename}")
        written.add(output_path)

    return written


def write_manifests(
    content: str,
    output_dir: Path,
    namespace: str,
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
        app_name: If provided, written as a comment at the top of each file

    Returns:
        Set of paths written

    Raises:
        OSError: If files cannot be written
    """
    documents = _load_all_yaml(content)

    # Filter out Helm test hook documents and log them
    filtered_documents = []
    skipped_hooks = 0
    for doc in documents:
        kind = doc.get("kind", "unknown")
        annotations = doc.get("metadata", {}).get("annotations") or {}
        if not isinstance(annotations, dict):
            raise TypeError(
                f"failed to read annotations on object {kind} from {app_name}, "
                f"item annotations is not a dict"
            )
        hook_value = annotations.get("helm.sh/hook")
        if hook_value is not None:
            name = doc.get("metadata", {}).get("name")
            skipped_hooks += 1
            logger.debug(f"Skipping {kind} {name} (helm.sh/hook={hook_value})")
        else:
            filtered_documents.append(doc)
    documents = filtered_documents
    if skipped_hooks:
        logger.info(f"Skipped {skipped_hooks} helm hook objects")

    # Add namespace to namespaced resources that don't already have one
    for doc in documents:
        kind = doc.get("kind")
        if kind and kind not in CLUSTER_SCOPED_KINDS:
            if "namespace" not in doc.get("metadata", {}):
                doc.setdefault("metadata", {})["namespace"] = namespace

    return _write_documents(documents, output_dir, namespace, app_name)
