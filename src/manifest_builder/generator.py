# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Manifest generation orchestration."""

import logging
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pystache
import yaml
from pystache.common import MissingTags

from manifest_builder.config import (
    ChartConfig,
    ManifestConfig,
    ManifestConfigs,
    TemplateValue,
    validate_chart_config,
)
from manifest_builder.handlers import ConfigHandler, GenerationContext
from manifest_builder.helm import ChartCacheStats, pull_chart, run_helm_template

logger = logging.getLogger(__name__)

YAML_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
YAML_DUMPER: type[yaml.Dumper] = yaml.Dumper


def plural(num: int, plural_form: str = "s") -> str:
    return plural_form if num != 1 else ""


class ManifestError(Exception):
    """Raised when manifest generation fails for a specific config."""

    def __init__(self, config_name: str, cause: Exception) -> None:
        self.config_name = config_name
        self.cause = cause
        super().__init__(f"{type(cause).__name__}: {cause}")


class HelmConfigHandler(ConfigHandler):
    """Generate manifests for Helm chart configs."""

    def __init__(self, configs: Sequence[ChartConfig] | None = None) -> None:
        self.configs = list(configs or [])

    def top_level_config_name(self) -> str:
        return "helm"

    def load_config(
        self,
        data: object,
        source_file: Path,
        root_config: dict[str, Any],
    ) -> None:
        if not isinstance(data, list):
            raise ValueError(f"'helm' must be a list of tables in {source_file}")

        variables = _parse_variables(root_config.get("variables"), source_file)
        for item in data:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Each [[helm]] entry must be a table in {source_file}"
                )
            self.configs.append(_parse_chart_config(item, source_file, variables))

    def iter_configs(self) -> Sequence[ManifestConfig]:
        return self.configs

    def resolve(self, helmfile: object | None) -> None:
        if not any(config.release for config in self.configs):
            return

        if helmfile is None:
            names = [config.name for config in self.configs if config.release]
            raise ValueError(
                f"Charts {names} reference helmfile releases but no releases.yaml was found"
            )

        release_data = _get_helmfile_data(helmfile)
        repo_by_name = release_data[0]
        release_by_name = release_data[1]

        resolved: list[ChartConfig] = []
        for config in self.configs:
            if config.release is None:
                resolved.append(config)
                continue

            release_name = config.release
            if release_name not in release_by_name:
                raise ValueError(f"Release '{release_name}' not found in releases.yaml")

            hf_release = release_by_name[release_name]

            parts = hf_release.chart.split("/", 1)
            if len(parts) != 2:
                raise ValueError(
                    f"helmfile release '{release_name}' chart '{hf_release.chart}' "
                    "must be in 'reponame/chartname' format"
                )
            repo_name, chart_name = parts

            if repo_name not in repo_by_name:
                raise ValueError(
                    f"Repository '{repo_name}' referenced by release '{release_name}' "
                    "not found in releases.yaml repositories"
                )

            repo_url = repo_by_name[repo_name]

            if repo_url.startswith("oci://"):
                base_url = repo_url.rstrip("/")
                resolved_chart = f"{base_url}/{chart_name}"
                resolved_repo = None
            else:
                resolved_chart = chart_name
                resolved_repo = repo_url

            resolved.append(
                ChartConfig(
                    name=config.name,
                    namespace=config.namespace,
                    chart=resolved_chart,
                    repo=resolved_repo,
                    version=hf_release.version,
                    values=config.values,
                    variables=config.variables,
                    release=config.release,
                    extra_resources=config.extra_resources,
                    init=config.init,
                )
            )

        self.configs = resolved

    def validate(self, config: ManifestConfig, repo_root: Path) -> None:
        if not isinstance(config, ChartConfig):
            raise TypeError(f"HelmConfigHandler cannot process {type(config).__name__}")
        validate_chart_config(config, repo_root)

    def generate(
        self,
        config: ManifestConfig,
        context: GenerationContext,
    ) -> set[Path]:
        if not isinstance(config, ChartConfig):
            raise TypeError(f"HelmConfigHandler cannot process {type(config).__name__}")
        return _generate_helm_manifests(
            config,
            context.output_dir,
            context.charts_dir,
            context.verbose,
            images=context.images,
            cache_stats=context.cache_stats,
        )


def _parse_chart_config(
    data: dict,
    source_file: Path,
    variables: dict[str, TemplateValue],
) -> ChartConfig:
    """Parse a single Helm chart configuration from TOML data."""
    has_release = "release" in data
    has_chart = "chart" in data

    if has_release and has_chart:
        raise ValueError(f"Cannot specify both 'release' and 'chart' in {source_file}")
    if not has_release and not has_chart:
        raise ValueError(f"Must specify either 'release' or 'chart' in {source_file}")
    if "namespace" not in data:
        raise ValueError(f"Missing required field 'namespace' in {source_file}")

    config_dir = source_file.parent
    values = [config_dir / v for v in data.get("values", [])]

    extra_resources = None
    if "extra-resources" in data:
        extra_resources = config_dir / data["extra-resources"]

    init = None
    if "init" in data:
        init = config_dir / data["init"]

    if has_release:
        return ChartConfig(
            name=data["release"],
            namespace=data["namespace"],
            chart=None,
            repo=None,
            version=None,
            values=values,
            variables=variables.copy(),
            release=data["release"],
            extra_resources=extra_resources,
            init=init,
        )

    if "name" not in data:
        raise ValueError(f"Missing required field 'name' in {source_file}")
    return ChartConfig(
        name=data["name"],
        namespace=data["namespace"],
        chart=data["chart"],
        repo=data.get("repo"),
        version=data.get("version"),
        values=values,
        variables=variables.copy(),
        release=None,
        extra_resources=extra_resources,
        init=init,
    )


def _parse_variables(
    data: object,
    source_file: Path,
) -> dict[str, TemplateValue]:
    """Parse top-level variables for values file templating."""
    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ValueError(f"'variables' must be a table in {source_file}")

    variables: dict[str, TemplateValue] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise ValueError(f"Variable keys in {source_file} must be strings")
        if not isinstance(value, str | int | float | bool):
            raise ValueError(
                f"Variable '{key}' in {source_file} must be a string, number, or boolean"
            )
        variables[key] = value

    return variables


def _get_helmfile_data(helmfile: object) -> tuple[dict[str, str], dict[str, Any]]:
    repositories = getattr(helmfile, "repositories")
    releases = getattr(helmfile, "releases")
    repo_by_name = {
        repo.name: (f"oci://{repo.url}" if repo.oci else repo.url)
        for repo in repositories
    }
    release_by_name = {release.name: release for release in releases}
    return repo_by_name, release_by_name


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
                logger.info(
                    f"Copied {len(crd_docs)} CRD{plural(len(crd_docs))} in {elapsed:.2f}s"
                )

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


@dataclass(frozen=True)
class _GenerationJob:
    handler: ConfigHandler
    config: ManifestConfig


def _collect_generation_jobs(
    configs: ManifestConfigs,
) -> list[_GenerationJob]:
    """Pair each loaded config with exactly one registered handler."""
    all_configs = configs.all_configs()
    seen_config_ids: set[int] = set()
    jobs: list[_GenerationJob] = []

    for handler in configs.handlers:
        for config in handler.iter_configs():
            config_id = id(config)
            if config_id in seen_config_ids:
                raise ValueError(
                    f"Multiple config handlers selected '{config.name}' "
                    f"({config.namespace})"
                )
            seen_config_ids.add(config_id)
            jobs.append(_GenerationJob(handler=handler, config=config))

    missing = [config for config in all_configs if id(config) not in seen_config_ids]
    if missing:
        details = ", ".join(f"{config.name} ({config.namespace})" for config in missing)
        raise ValueError(f"No config handler registered for: {details}")

    return jobs


def generate_manifests(
    configs: ManifestConfigs,
    output_dir: Path,
    repo_root: Path,
    *,
    images: dict[str, str] | None = None,
    charts_dir: Path | None = None,
    verbose: bool = False,
    owned_namespaces: set[str] | None = None,
) -> set[Path]:
    """
    Generate manifests for all configured apps.

    Args:
        configs: App configurations grouped by type
        output_dir: Directory to write generated manifests
        repo_root: Repository root for resolving relative paths
        images: Dict mapping image variable names to image references for template rendering
        charts_dir: Directory for caching pulled charts (default: repo_root/.charts)
        verbose: If True, log detailed output
        owned_namespaces: Namespaces owned by other services/pipelines. Files
            in these namespace directories are not cleaned up, and generation
            fails if any output would land in one of them.

    Returns:
        Set of paths that were written

    Raises:
        ValueError: If configuration validation fails
        RuntimeError: If manifest generation fails
    """
    if not configs:
        logger.info("No configs configured")
        return set()

    if charts_dir is None:
        charts_dir = Path.home() / ".cache" / "manifest-builder"

    owned_namespaces = owned_namespaces or set()
    jobs = _collect_generation_jobs(configs)

    # Validate all of the configs first
    for job in jobs:
        config = job.config
        job.handler.validate(config, repo_root)
        if config.namespace in owned_namespaces:
            raise ValueError(
                f"Config '{config.name}' targets namespace '{config.namespace}' "
                f"which is owned by another service (listed in owners/)"
            )

    # Generate manifests
    # Map the output paths to the config name that generated them
    written_paths: dict[Path, str] = {}
    cache_stats = ChartCacheStats()
    context = GenerationContext(
        output_dir=output_dir,
        repo_root=repo_root,
        charts_dir=charts_dir,
        verbose=verbose,
        images=images,
        cache_stats=cache_stats,
    )
    for job in jobs:
        config = job.config
        try:
            logger.info(f"Generating manifest for {config.name} ({config.namespace})")
            paths = job.handler.generate(config, context)

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

            count = len(paths)
            logger.info(
                f"✓ {config.name} ({config.namespace}) -> {count} file{plural(count)}"
            )

        except ManifestError:
            raise
        except Exception as e:
            logger.error(f"✗ {config.name} ({config.namespace})")
            raise ManifestError(config.name, e) from e

    # Catch any output that landed in an owned namespace via metadata override
    if owned_namespaces:
        intrusions = sorted(
            (path, source)
            for path, source in written_paths.items()
            if _path_namespace(path, output_dir) in owned_namespaces
        )
        if intrusions:
            details = "\n  ".join(
                f"{path} (from {source})" for path, source in intrusions
            )
            raise ValueError(
                "Generated output would land in a namespace owned by another "
                f"service:\n  {details}"
            )

    # Create Namespace objects for any namespace that lacks one
    namespace_paths = _ensure_namespaces(output_dir, written_paths, owned_namespaces)
    written_paths.update(namespace_paths)

    # Remove any stale files left over from the previous runs
    _cleanup_stale_files(output_dir, written_paths, owned_namespaces)

    if cache_stats.hits or cache_stats.misses:
        logger.info(
            f"Chart cache: {cache_stats.hits} hit{plural(cache_stats.hits)}, "
            f"{cache_stats.misses} miss{plural(cache_stats.misses, 'es')}"
        )

    total = len(written_paths)
    summary = f"Done! Generated {total} manifest{plural(total)}"
    removed = _count_removed_files(output_dir, written_paths, owned_namespaces)
    if removed:
        summary += f", removed {removed} stale file{plural(removed)}"
    logger.info(summary)

    return set(written_paths.keys())


def _path_namespace(path: Path, output_dir: Path) -> str | None:
    """Return the top-level namespace directory for ``path`` under ``output_dir``."""
    try:
        rel_parts = path.relative_to(output_dir).parts
    except ValueError:
        return None
    return rel_parts[0] if rel_parts else None


def _ensure_namespaces(
    output_dir: Path,
    written_paths: dict[Path, str],
    owned_namespaces: set[str] | None = None,
) -> dict[Path, str]:
    """Create Namespace objects for namespace directories that lack one.

    For each subdirectory of output_dir (excluding cluster/), checks whether a
    Namespace resource already exists for that namespace. If not, writes a minimal
    Namespace manifest to <output_dir>/<namespace>/namespace-<namespace>.yaml.

    Args:
        output_dir: Base output directory
        written_paths: Paths written so far in this run
        owned_namespaces: Namespaces owned by other services; skipped here

    Returns:
        Dict of newly created namespace paths mapped to the source label
    """
    if not output_dir.exists():
        return {}

    owned = owned_namespaces or set()
    new_paths: dict[Path, str] = {}
    for ns_dir in sorted(output_dir.iterdir()):
        if (
            not ns_dir.is_dir()
            or ns_dir.name == "cluster"
            or ns_dir.name == "kube-system"
            or ns_dir.name in owned
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


def _cleanup_stale_files(
    output_dir: Path,
    written_paths: dict[Path, str],
    owned_namespaces: set[str] | None = None,
) -> None:
    """Remove stale files and empty directories from previous runs.

    Args:
        output_dir: Directory to clean
        written_paths: Set of paths that were written in this run
        owned_namespaces: Namespaces owned by other services; their files
            and directories are left untouched.
    """
    if not output_dir.exists():
        return

    owned = owned_namespaces or set()
    for existing in output_dir.rglob("*.yaml"):
        if _path_namespace(existing, output_dir) in owned:
            continue
        if existing not in written_paths:
            existing.unlink()
            logger.debug(f"Removed {existing.relative_to(output_dir)}")

    # Remove any empty directories, except those owned by other services
    for directory in sorted(output_dir.rglob("*"), reverse=True):
        if not directory.is_dir():
            continue
        if _path_namespace(directory, output_dir) in owned:
            continue
        if not any(directory.iterdir()):
            directory.rmdir()


def _count_removed_files(
    output_dir: Path,
    written_paths: dict[Path, str],
    owned_namespaces: set[str] | None = None,
) -> int:
    """Count the number of stale files that were removed.

    Args:
        output_dir: Directory to check
        written_paths: Set of paths that were written in this run
        owned_namespaces: Namespaces owned by other services; not counted.

    Returns:
        Number of removed files
    """
    if not output_dir.exists():
        return 0

    owned = owned_namespaces or set()
    removed = 0
    for existing in output_dir.rglob("*.yaml"):
        if _path_namespace(existing, output_dir) in owned:
            continue
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
