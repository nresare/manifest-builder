# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Configuration parsing and validation for manifest-builder."""

import tomllib
from dataclasses import dataclass
from importlib.resources import files
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


@dataclass
class WebsiteConfig:
    """Configuration for a website app built from bundled YAML templates."""

    name: str
    namespace: str
    hugo_repo: str | None = None
    image: str | None = None
    args: str | list[str] | None = None
    config_files: dict[str, Path] | None = None  # container path -> resolved local path


def load_configs(config_dir: Path) -> list[ChartConfig | WebsiteConfig]:
    """
    Load all app configurations from TOML files in the config directory.

    Each TOML file may contain [[helms]] and [[websites]] tables.

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

    configs: list[ChartConfig | WebsiteConfig] = []
    toml_files = list(config_dir.rglob("*.toml"))

    if not toml_files:
        raise FileNotFoundError(f"No TOML files found in {config_dir}")

    for toml_file in toml_files:
        with open(toml_file, "rb") as f:
            data = tomllib.load(f)

        if "helms" not in data and "websites" not in data:
            raise ValueError(f"No [[helms]] or [[websites]] entries found in {toml_file}")

        for helm_data in data.get("helms", []):
            configs.append(_parse_chart_config(helm_data, toml_file))

        for website_data in data.get("websites", []):
            configs.append(_parse_website_config(website_data, toml_file))

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

    if has_release:
        return ChartConfig(
            name=data["release"],
            namespace=data["namespace"],
            chart=None,
            repo=None,
            version=None,
            values=values,
            release=data["release"],
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
        )


def _parse_website_config(data: dict, source_file: Path) -> WebsiteConfig:
    """Parse a website app configuration from TOML data."""
    for field in ("name", "namespace"):
        if field not in data:
            raise ValueError(f"Missing required field '{field}' in {source_file}")

    hugo_repo = data.get("hugo_repo")
    image = data.get("image")

    if hugo_repo and image:
        raise ValueError(
            f"Cannot specify both 'hugo_repo' and 'image' in {source_file}"
        )

    # Parse config_files: resolve local paths relative to the TOML file's directory
    config_dir = source_file.parent
    config_files = None
    if "config_files" in data:
        config_files = {
            container_path: config_dir / local_path
            for container_path, local_path in data["config_files"].items()
        }

    return WebsiteConfig(
        name=data["name"],
        namespace=data["namespace"],
        hugo_repo=hugo_repo,
        image=image,
        args=data.get("args"),
        config_files=config_files,
    )


def resolve_configs(
    configs: list[ChartConfig | WebsiteConfig], helmfile: Helmfile | None
) -> list[ChartConfig | WebsiteConfig]:
    """
    Resolve helmfile release references, filling in chart/repo/version.

    Configs without a release reference are returned unchanged.

    Args:
        configs: App configs as parsed from TOML
        helmfile: Parsed helmfile.yaml, or None if not present

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
            f"Charts {names} reference helmfile releases but no helmfile.yaml was found"
        )

    repo_by_name = {r.name: r.url for r in helmfile.repositories}
    release_by_name = {r.name: r for r in helmfile.releases}

    resolved: list[ChartConfig | WebsiteConfig] = []
    for config in configs:
        if not isinstance(config, ChartConfig) or config.release is None:
            resolved.append(config)
            continue

        release_name = config.release
        if release_name not in release_by_name:
            raise ValueError(f"Release '{release_name}' not found in helmfile.yaml")

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
                "not found in helmfile.yaml repositories"
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
            )
        )

    return resolved


def validate_config(config: ChartConfig | WebsiteConfig, repo_root: Path) -> None:
    """
    Validate an app configuration.

    Args:
        config: App configuration to validate
        repo_root: Repository root directory for resolving relative paths

    Raises:
        ValueError: If validation fails
    """
    if isinstance(config, WebsiteConfig):
        for container_path, local_path in (config.config_files or {}).items():
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

    if config.chart is not None and (
        config.chart.startswith("./") or config.chart.startswith("/")
    ):
        chart_path = repo_root / config.chart
        if not chart_path.exists():
            raise ValueError(
                f"Local chart path not found for '{config.name}': {config.chart}"
            )
