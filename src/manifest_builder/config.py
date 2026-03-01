# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Configuration parsing and validation for manifest-builder."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from manifest_builder.helmfile import Helmfile


@dataclass
class ChartConfig:
    """Configuration for a single Helm chart."""

    name: str
    namespace: str
    chart: str | None  # None when using a helmfile release reference
    repo: str | None
    version: str | None
    values: list[Path]
    release: str | None  # helmfile release name; None for direct chart entries
    extra_resources: Path | None = (
        None  # directory with additional YAML resources to include
    )


@dataclass
class WebsiteConfig:
    """Configuration for a website app built from bundled YAML templates."""

    name: str
    namespace: str
    hugo_repo: str | None = None
    image: str | None = None
    args: str | list[str] | None = None
    config: dict[str, Path] | None = None  # container path -> resolved local path
    extra_hostnames: str | list[str] | None = (
        None  # additional hostnames for certificates/listeners
    )
    external_secrets: list[str] | None = (
        None  # mount paths for external secrets (e.g., ["/email-password"])
    )


@dataclass
class SimpleConfig:
    """Configuration for a simple app that copies existing manifests verbatim."""

    name: str
    namespace: str
    copy_from: Path  # resolved directory containing manifests to copy
    config: dict[str, Path] | None = None  # container path -> resolved local path


def load_images(config_dir: Path) -> dict[str, str]:
    """
    Load container image definitions from images.toml in the config directory.

    The images.toml file should have the format:
        [git]
        repo = "alpine/git"
        version = "2.47.2"

        [hugo]
        repo = "floryn90/hugo"
        version = "0.155.3-alpine"

    Returns a dict mapping template variable names to image references, e.g.:
        {"git_image": "alpine/git:2.47.2", "hugo_image": "floryn90/hugo:0.155.3-alpine"}

    Args:
        config_dir: Directory containing images.toml

    Returns:
        Dict mapping image variable names to full image references (repo:version)

    Raises:
        FileNotFoundError: If images.toml is not found in config_dir
        ValueError: If images.toml is invalid or missing required fields
    """
    images_file = config_dir / "images.toml"
    if not images_file.exists():
        raise FileNotFoundError(f"images.toml not found in {config_dir}")

    data = tomllib.loads(images_file.read_text())

    if not data:
        raise ValueError(f"images.toml is empty in {config_dir}")

    result = {}
    for key, image_def in data.items():
        if (
            not isinstance(image_def, dict)
            or "repo" not in image_def
            or "version" not in image_def
        ):
            raise ValueError(
                f"Each image in images.toml must have 'repo' and 'version' fields. "
                f"Invalid entry: {key}"
            )
        var_name = key.replace("-", "_") + "_image"
        result[var_name] = f"{image_def['repo']}:{image_def['version']}"

    return result


def load_configs(config_dir: Path) -> list[ChartConfig | WebsiteConfig | SimpleConfig]:
    """
    Load all app configurations from TOML files in the config directory.

    Each TOML file may contain [[helm]], [[website]], and [[simple]] tables.

    Args:
        config_dir: Directory containing TOML configuration files

    Returns:
        List of app config objects

    Raises:
        FileNotFoundError: If config_dir doesn't exist or contains no TOML files
        ValueError: If TOML is invalid or missing required fields
    """
    if not config_dir.exists():
        raise FileNotFoundError(f"Configuration directory not found: {config_dir}")

    if not config_dir.is_dir():
        raise ValueError(f"Configuration path is not a directory: {config_dir}")

    configs: list[ChartConfig | WebsiteConfig | SimpleConfig] = []
    toml_files = [f for f in config_dir.glob("*.toml") if f.name != "images.toml"]

    if not toml_files:
        raise FileNotFoundError(f"No TOML files found in {config_dir}")

    for toml_file in toml_files:
        with open(toml_file, "rb") as f:
            data = tomllib.load(f)

        if "helm" not in data and "website" not in data and "simple" not in data:
            raise ValueError(
                f"No [[helm]], [[website]], or [[simple]] entries found in {toml_file}"
            )

        for helm_data in data.get("helm", []):
            configs.append(_parse_chart_config(helm_data, toml_file))

        for website_data in data.get("website", []):
            configs.append(_parse_website_config(website_data, toml_file))

        for simple_data in data.get("simple", []):
            configs.append(_parse_simple_config(simple_data, toml_file))

    return configs


def _parse_chart_config(data: dict, source_file: Path) -> ChartConfig:
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

    # Parse extra_resources: resolve path relative to the TOML file's directory
    extra_resources = None
    if "extra-resources" in data:
        extra_resources = config_dir / data["extra-resources"]

    if has_release:
        return ChartConfig(
            name=data["release"],
            namespace=data["namespace"],
            chart=None,
            repo=None,
            version=None,
            values=values,
            release=data["release"],
            extra_resources=extra_resources,
        )
    else:
        if "name" not in data:
            raise ValueError(f"Missing required field 'name' in {source_file}")
        return ChartConfig(
            name=data["name"],
            namespace=data["namespace"],
            chart=data["chart"],
            repo=data.get("repo"),
            version=data.get("version"),
            values=values,
            release=None,
            extra_resources=extra_resources,
        )


def _parse_website_config(data: dict, source_file: Path) -> WebsiteConfig:
    """Parse a website app configuration from TOML data."""
    for field in ("name", "namespace"):
        if field not in data:
            raise ValueError(f"Missing required field '{field}' in {source_file}")

    hugo_repo = data.get("hugo-repo")
    image = data.get("image")

    if hugo_repo and image:
        raise ValueError(
            f"Cannot specify both 'hugo-repo' and 'image' in {source_file}"
        )

    # Parse config: resolve local paths relative to the TOML file's directory
    config_dir = source_file.parent
    config_dict = None
    if "config" in data:
        config_dict = {
            container_path: config_dir / local_path
            for container_path, local_path in data["config"].items()
        }

    # Parse external_secrets: normalize to list
    external_secrets = data.get("external-secrets")
    if external_secrets is not None and isinstance(external_secrets, str):
        external_secrets = [external_secrets]

    return WebsiteConfig(
        name=data["name"],
        namespace=data["namespace"],
        hugo_repo=hugo_repo,
        image=image,
        args=data.get("args"),
        config=config_dict,
        extra_hostnames=data.get("extra-hostnames"),
        external_secrets=external_secrets,
    )


def _parse_simple_config(data: dict, source_file: Path) -> SimpleConfig:
    """Parse a simple app configuration from TOML data."""
    for field in ("namespace", "copy-from"):
        if field not in data:
            raise ValueError(f"Missing required field '{field}' in {source_file}")

    config_dir = source_file.parent
    name = data.get("name", data["namespace"])
    copy_from = config_dir / data["copy-from"]

    config_dict = None
    config_data = data.get("config")
    if config_data is not None:
        # Support both [simple.config] (dict) and [[simple.config]] (list of dicts)
        if isinstance(config_data, list):
            merged: dict[str, str] = {}
            for item in config_data:
                merged.update(item)
            config_data = merged
        config_dict = {
            container_path: config_dir / local_path
            for container_path, local_path in config_data.items()
        }

    return SimpleConfig(
        name=name,
        namespace=data["namespace"],
        copy_from=copy_from,
        config=config_dict,
    )


def resolve_configs(
    configs: list[ChartConfig | WebsiteConfig | SimpleConfig],
    helmfile: Helmfile | None,
) -> list[ChartConfig | WebsiteConfig | SimpleConfig]:
    """
    Resolve helmfile release references, filling in chart/repo/version.

    Configs without a release reference are returned unchanged.

    Args:
        configs: App configs as parsed from TOML
        helmfile: Parsed releases.yaml, or None if not present

    Returns:
        Configs with all release references resolved

    Raises:
        ValueError: If a release reference cannot be resolved
    """
    if not any(isinstance(c, ChartConfig) and c.release for c in configs):
        return configs

    if helmfile is None:
        names = [c.name for c in configs if isinstance(c, ChartConfig) and c.release]
        raise ValueError(
            f"Charts {names} reference helmfile releases but no releases.yaml was found"
        )

    repo_by_name = {r.name: r.url for r in helmfile.repositories}
    release_by_name = {r.name: r for r in helmfile.releases}

    resolved: list[ChartConfig | WebsiteConfig | SimpleConfig] = []
    for config in configs:
        if not isinstance(config, ChartConfig) or config.release is None:
            resolved.append(config)
            continue

        release_name = config.release
        if release_name not in release_by_name:
            raise ValueError(f"Release '{release_name}' not found in releases.yaml")

        hf_release = release_by_name[release_name]

        # Handle both traditional (repo/chart) and OCI (chart-only with repo name match) formats
        parts = hf_release.chart.split("/", 1)
        if len(parts) == 2:
            # Traditional format: reponame/chartname
            repo_name, chart_name = parts
        else:
            # OCI or single-name format: try to match chart name to a repository
            chart_name = hf_release.chart
            repo_name = chart_name

        if repo_name not in repo_by_name:
            raise ValueError(
                f"Repository '{repo_name}' referenced by release '{release_name}' "
                "not found in releases.yaml repositories"
            )

        resolved.append(
            ChartConfig(
                name=config.name,
                namespace=config.namespace,
                chart=chart_name,
                repo=repo_by_name[repo_name],
                version=hf_release.version,
                values=config.values,
                release=config.release,
                extra_resources=config.extra_resources,
            )
        )

    return resolved


def validate_config(
    config: ChartConfig | WebsiteConfig | SimpleConfig, repo_root: Path
) -> None:
    """
    Validate an app configuration.

    Args:
        config: App configuration to validate
        repo_root: Repository root directory for resolving relative paths

    Raises:
        ValueError: If validation fails
    """
    if isinstance(config, WebsiteConfig):
        for container_path, local_path in (config.config or {}).items():
            if not local_path.exists():
                raise ValueError(
                    f"Config file not found for '{config.name}': {local_path} "
                    f"(mapped from {container_path})"
                )
        return

    if isinstance(config, SimpleConfig):
        if not config.copy_from.exists():
            raise ValueError(
                f"copy-from directory not found for '{config.name}': {config.copy_from}"
            )
        if not config.copy_from.is_dir():
            raise ValueError(
                f"copy-from path is not a directory for '{config.name}': {config.copy_from}"
            )
        for container_path, local_path in (config.config or {}).items():
            if not local_path.exists():
                raise ValueError(
                    f"Config file not found for '{config.name}': {local_path} "
                    f"(mapped from {container_path})"
                )
        return

    for values_path in config.values:
        if not values_path.exists():
            raise ValueError(
                f"Values file not found for chart '{config.name}': {values_path}"
            )

    if config.extra_resources is not None:
        if not config.extra_resources.exists():
            raise ValueError(
                f"Extra resources directory not found for '{config.name}': {config.extra_resources}"
            )
        if not config.extra_resources.is_dir():
            raise ValueError(
                f"Extra resources path is not a directory for '{config.name}': {config.extra_resources}"
            )

    if config.chart is not None and (
        config.chart.startswith("./") or config.chart.startswith("/")
    ):
        chart_path = repo_root / config.chart
        if not chart_path.exists():
            raise ValueError(
                f"Local chart path not found for '{config.name}': {config.chart}"
            )
