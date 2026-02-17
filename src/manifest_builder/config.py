"""Configuration parsing and validation for manifest-builder."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChartConfig:
    """Configuration for a single Helm chart."""

    name: str
    namespace: str
    chart: str
    version: str | None
    values: list[str]


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
    required_fields = ["name", "namespace", "chart"]
    missing_fields = [field for field in required_fields if field not in data]

    if missing_fields:
        raise ValueError(
            f"Missing required fields in {source_file}: {', '.join(missing_fields)}"
        )

    return ChartConfig(
        name=data["name"],
        namespace=data["namespace"],
        chart=data["chart"],
        version=data.get("version"),
        values=data.get("values", []),
    )


def validate_config(config: ChartConfig, repo_root: Path) -> None:
    """
    Validate a chart configuration.

    Args:
        config: Chart configuration to validate
        repo_root: Repository root directory for resolving relative paths

    Raises:
        ValueError: If validation fails
    """
    # Validate that values files exist (if they're local paths)
    for values_file in config.values:
        values_path = repo_root / values_file
        if not values_path.exists():
            raise ValueError(
                f"Values file not found for chart '{config.name}': {values_file}"
            )

    # Validate chart if it's a local path
    if config.chart.startswith("./") or config.chart.startswith("/"):
        chart_path = repo_root / config.chart
        if not chart_path.exists():
            raise ValueError(
                f"Local chart path not found for '{config.name}': {config.chart}"
            )
