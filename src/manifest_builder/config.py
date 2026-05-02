# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Configuration parsing and validation for manifest-builder."""

import tomllib
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from manifest_builder.helmfile import Helmfile

if TYPE_CHECKING:
    from collections.abc import Sequence

    from manifest_builder.handlers import ConfigHandler

DEFAULT_REPLICA_COUNT = 2
TemplateValue = str | int | float | bool


def validate_known_fields(
    table_name: str,
    data: dict,
    allowed_fields: Collection[str],
    source_file: Path,
    table_index: int = 0,
) -> None:
    """Raise if a parsed TOML table contains fields the parser does not know."""
    unknown = sorted(set(data) - set(allowed_fields))
    if not unknown:
        return

    fields = ", ".join(
        _format_field_location(field, source_file, table_name, table_index)
        for field in unknown
    )
    suffix = "s" if len(unknown) != 1 else ""
    raise ValueError(
        f"Unknown field{suffix} in {table_name}: {fields} in {source_file}"
    )


def _format_field_location(
    field: str,
    source_file: Path,
    table_name: str | None = None,
    table_index: int = 0,
) -> str:
    line_number = _find_field_line(source_file, field, table_name, table_index)
    if line_number is None:
        return repr(field)
    return f"{field!r} on line {line_number}"


def _find_field_line(
    source_file: Path,
    field: str,
    table_name: str | None = None,
    table_index: int = 0,
) -> int | None:
    lines = source_file.read_text().splitlines()
    if table_name is None:
        return _find_top_level_field_line(lines, field)

    in_table = False
    current_index = -1
    for line_number, line in enumerate(lines, start=1):
        stripped = _strip_toml_comment(line).strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if stripped == table_name:
                current_index += 1
                in_table = current_index == table_index
                continue
            if in_table:
                return None
            continue
        if in_table and _line_defines_toml_key(stripped, field):
            return line_number

    return None


def _find_top_level_field_line(lines: list[str], field: str) -> int | None:
    in_table = False
    for line_number, line in enumerate(lines, start=1):
        stripped = _strip_toml_comment(line).strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            if stripped in {f"[{field}]", f"[[{field}]]"}:
                return line_number
            in_table = True
            continue
        if not in_table and _line_defines_toml_key(stripped, field):
            return line_number
    return None


def _strip_toml_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None:
            return line[:index]
    return line


def _line_defines_toml_key(stripped_line: str, field: str) -> bool:
    if "=" not in stripped_line:
        return False
    key = stripped_line.split("=", 1)[0].strip()
    return key == field


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
    variables: dict[str, TemplateValue] = field(default_factory=dict)
    extra_resources: Path | None = (
        None  # directory with additional YAML resources to include
    )
    init: Path | None = None  # optional shell script to inject as initContainer


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
    custom_token_audience: str | None = None
    persistence: dict[str, str] | None = None  # mount path -> storage request size
    replicas: int = DEFAULT_REPLICA_COUNT  # number of deployment replicas


@dataclass
class SimpleConfig:
    """Configuration for a simple deployment built from bundled YAML templates."""

    name: str
    namespace: str
    image: str
    args: str | list[str] | None = None
    iam_role: str | None = None
    k8s_role: str | None = None
    config: dict[str, Path] | None = None  # container path -> resolved local path
    variables: dict[str, TemplateValue] = field(default_factory=dict)
    extra_resources: Path | None = (
        None  # directory with additional YAML resources to include
    )
    replicas: int = DEFAULT_REPLICA_COUNT  # number of deployment replicas


@dataclass
class CopyConfig:
    """Configuration for an app that copies existing manifests verbatim."""

    name: str
    namespace: str
    source: Path  # resolved directory containing manifests to copy
    config: dict[str, Path] | None = None  # container path -> resolved local path


type ManifestConfig = ChartConfig | WebsiteConfig | SimpleConfig | CopyConfig


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

    If images.toml is absent, returns an empty dict so image overrides remain optional.

    Args:
        config_dir: Directory containing images.toml

    Returns:
        Dict mapping image variable names to full image references (repo:version)

    Raises:
        ValueError: If images.toml is invalid or missing required fields
    """
    images_file = config_dir / "images.toml"
    if not images_file.exists():
        return {}

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


def load_owned_namespaces(config_dir: Path) -> set[str]:
    """Load the set of namespaces owned by other services or pipelines.

    Reads ``<config_dir>/owners/*.toml``. Each file may declare ownership via
    a ``namespace`` string or a ``namespaces`` list of strings (or both).
    Returns an empty set if the ``owners`` directory does not exist.
    """
    owners_dir = config_dir / "owners"
    if not owners_dir.is_dir():
        return set()

    owned: set[str] = set()
    for toml_file in sorted(owners_dir.glob("*.toml")):
        data = tomllib.loads(toml_file.read_text())

        ns = data.get("namespace")
        if ns is not None:
            if not isinstance(ns, str):
                raise ValueError(f"'namespace' must be a string in {toml_file}")
            owned.add(ns)

        ns_list = data.get("namespaces")
        if ns_list is not None:
            if not isinstance(ns_list, list) or not all(
                isinstance(n, str) for n in ns_list
            ):
                raise ValueError(
                    f"'namespaces' must be a list of strings in {toml_file}"
                )
            owned.update(ns_list)

    return owned


def load_configs(
    config_dir: Path, handlers: "Sequence[ConfigHandler]"
) -> "Sequence[ConfigHandler]":
    """
    Load app configurations from config.toml in the config directory.

    The config.toml file may contain top-level tables owned by the supplied
    config handlers.

    Args:
        config_dir: Directory containing TOML configuration files

    Returns:
        Handlers populated with the config items they own

    Raises:
        FileNotFoundError: If config_dir or config.toml doesn't exist
        ValueError: If TOML is invalid or missing required fields
    """
    if not config_dir.exists():
        raise FileNotFoundError(f"Configuration directory not found: {config_dir}")

    if not config_dir.is_dir():
        raise ValueError(f"Configuration path is not a directory: {config_dir}")

    toml_file = config_dir / "config.toml"
    if not toml_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {toml_file}")

    with open(toml_file, "rb") as f:
        data = tomllib.load(f)

    handler_by_name: dict[str, ConfigHandler] = {}
    for handler in handlers:
        name = handler.top_level_config_name()
        if name in handler_by_name:
            raise ValueError(f"Duplicate config handler for top-level key '{name}'")
        handler_by_name[name] = handler
    if not handler_by_name:
        raise ValueError("No config handlers registered")

    allowed_top_level = set(handler_by_name) | {"variables"}
    unknown_top_level = sorted(set(data) - allowed_top_level)
    if unknown_top_level:
        fields = ", ".join(
            _format_field_location(field, toml_file) for field in unknown_top_level
        )
        suffix = "s" if len(unknown_top_level) != 1 else ""
        raise ValueError(f"Unknown top-level field{suffix}: {fields} in {toml_file}")

    present_handler_names = sorted(name for name in handler_by_name if name in data)
    if not present_handler_names:
        expected = ", ".join(f"[[{name}]]" for name in sorted(handler_by_name))
        raise ValueError(f"No {expected} entries found in {toml_file}")

    for name in present_handler_names:
        handler_by_name[name].load_config(data[name], toml_file, data)

    return handlers


def resolve_configs(
    handlers: "Sequence[ConfigHandler]",
    helmfile: Helmfile | None,
) -> "Sequence[ConfigHandler]":
    """
    Resolve helmfile release references, filling in chart/repo/version.

    Non-Helm configs and Helm configs without a release reference are returned
    unchanged.

    Args:
        handlers: Config handlers populated by load_configs()
        helmfile: Parsed releases.yaml, or None if not present

    Returns:
        Handlers with all release references resolved

    Raises:
        ValueError: If a release reference cannot be resolved
    """
    for handler in handlers:
        handler.resolve(helmfile)
    return handlers


def validate_website_config(config: WebsiteConfig) -> None:
    """Validate a website app configuration."""
    for container_path, local_path in (config.config or {}).items():
        if not local_path.exists():
            raise ValueError(
                f"Config file not found for '{config.name}': {local_path} "
                f"(mapped from {container_path})"
            )


def validate_simple_config(config: SimpleConfig) -> None:
    """Validate a simple app configuration."""
    for container_path, local_path in (config.config or {}).items():
        if not local_path.exists():
            raise ValueError(
                f"Config file not found for '{config.name}': {local_path} "
                f"(mapped from {container_path})"
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


def validate_copy_config(config: CopyConfig) -> None:
    """Validate a copy app configuration."""
    if not config.source.exists():
        raise ValueError(
            f"source directory not found for '{config.name}': {config.source}"
        )
    if not config.source.is_dir():
        raise ValueError(
            f"source path is not a directory for '{config.name}': {config.source}"
        )
    for container_path, local_path in (config.config or {}).items():
        if not local_path.exists():
            raise ValueError(
                f"Config file not found for '{config.name}': {local_path} "
                f"(mapped from {container_path})"
            )


def validate_chart_config(config: ChartConfig, repo_root: Path) -> None:
    """Validate a Helm chart configuration."""
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

    if config.init is not None and not config.init.exists():
        raise ValueError(f"init script not found for '{config.name}': {config.init}")


def validate_config(config: ManifestConfig, repo_root: Path) -> None:
    """
    Validate an app configuration.

    Kept as a compatibility helper for callers that already have a concrete
    config object. The main generation path uses config handlers instead.
    """
    if isinstance(config, WebsiteConfig):
        validate_website_config(config)
    elif isinstance(config, SimpleConfig):
        validate_simple_config(config)
    elif isinstance(config, CopyConfig):
        validate_copy_config(config)
    else:
        validate_chart_config(config, repo_root)
