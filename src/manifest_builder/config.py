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
    values: list[str]
    release: str | None  # helmfile release name; None for direct chart entries


def load_configs(config_dir: Path) -> list[ChartConfig]:
    """
    Load all chart configurations from TOML files in the config directory.

    Args:
        config_dir: Directory containing TOML configuration files

    Returns:
        List of ChartConfig objects

    Raises:
        FileNotFoundError: If config_dir doesn't exist
        ValueError: If TOML is invalid or missing required fields
    """
    if not config_dir.exists():
        raise FileNotFoundError(f"Configuration directory not found: {config_dir}")

    if not config_dir.is_dir():
        raise ValueError(f"Configuration path is not a directory: {config_dir}")

    configs: list[ChartConfig] = []
    toml_files = list(config_dir.rglob("*.toml"))

    if not toml_files:
        raise FileNotFoundError(f"No TOML files found in {config_dir}")

    for toml_file in toml_files:
        with open(toml_file, "rb") as f:
            data = tomllib.load(f)

        if "chart" not in data:
            raise ValueError(f"No [[chart]] entries found in {toml_file}")

        for chart_data in data["chart"]:
            config = _parse_chart_config(chart_data, toml_file)
            configs.append(config)

    return configs


def _parse_chart_config(data: dict, source_file: Path) -> ChartConfig:
    """Parse a single chart configuration from TOML data."""
    has_release = "release" in data
    has_chart = "chart" in data

    if has_release and has_chart:
        raise ValueError(f"Cannot specify both 'release' and 'chart' in {source_file}")
    if not has_release and not has_chart:
        raise ValueError(f"Must specify either 'release' or 'chart' in {source_file}")
    if "namespace" not in data:
        raise ValueError(f"Missing required field 'namespace' in {source_file}")

    if has_release:
        return ChartConfig(
            name=data["release"],
            namespace=data["namespace"],
            chart=None,
            repo=None,
            version=None,
            values=data.get("values", []),
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
            values=data.get("values", []),
            release=None,
        )


def resolve_configs(
    configs: list[ChartConfig], helmfile: Helmfile | None
) -> list[ChartConfig]:
    """
    Resolve helmfile release references, filling in chart/repo/version.

    Configs without a release reference are returned unchanged.

    Args:
        configs: Chart configs as parsed from TOML
        helmfile: Parsed helmfile.yaml, or None if not present

    Returns:
        Configs with all release references resolved

    Raises:
        ValueError: If a release reference cannot be resolved
    """
    if not any(c.release for c in configs):
        return configs

    if helmfile is None:
        names = [c.name for c in configs if c.release]
        raise ValueError(
            f"Charts {names} reference helmfile releases but no helmfile.yaml was found"
        )

    repo_by_name = {r.name: r.url for r in helmfile.repositories}
    release_by_name = {r.name: r for r in helmfile.releases}

    resolved: list[ChartConfig] = []
    for config in configs:
        if config.release is None:
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


def validate_config(config: ChartConfig, repo_root: Path) -> None:
    """
    Validate a chart configuration.

    Args:
        config: Chart configuration to validate
        repo_root: Repository root directory for resolving relative paths

    Raises:
        ValueError: If validation fails
    """
    for values_file in config.values:
        values_path = repo_root / values_file
        if not values_path.exists():
            raise ValueError(
                f"Values file not found for chart '{config.name}': {values_file}"
            )

    if config.chart is not None and (
        config.chart.startswith("./") or config.chart.startswith("/")
    ):
        chart_path = repo_root / config.chart
        if not chart_path.exists():
            raise ValueError(
                f"Local chart path not found for '{config.name}': {config.chart}"
            )
