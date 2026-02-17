"""Tests for configuration parsing and validation."""

import textwrap
from pathlib import Path

import pytest

from manifest_builder.config import (
    ChartConfig,
    load_configs,
    resolve_configs,
    validate_config,
)
from manifest_builder.helmfile import Helmfile, HelmfileRelease, HelmfileRepository


def write_toml(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(content))
    return path


# ---------------------------------------------------------------------------
# Values file path resolution
# ---------------------------------------------------------------------------


def test_values_resolved_relative_to_config_dir(tmp_path: Path) -> None:
    """Values paths must be resolved relative to the TOML file's directory."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[app]]
        type = "helm"
        namespace = "default"
        chart = "./charts/myapp"
        name = "myapp"
        values = ["myapp/values.yaml"]
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    assert configs[0].values == [conf_dir / "myapp/values.yaml"]


def test_values_resolved_relative_to_custom_config_dir(tmp_path: Path) -> None:
    """Specifying a different -c directory changes where values are resolved."""
    conf_a = tmp_path / "conf-a"
    conf_a.mkdir()
    conf_b = tmp_path / "conf-b"
    conf_b.mkdir()

    for conf_dir in (conf_a, conf_b):
        write_toml(
            conf_dir,
            "config.toml",
            """\
            [[app]]
            type = "helm"
            namespace = "default"
            chart = "./charts/myapp"
            name = "myapp"
            values = ["values.yaml"]
            """,
        )

    configs_a = load_configs(conf_a)
    configs_b = load_configs(conf_b)

    assert configs_a[0].values == [conf_a / "values.yaml"]
    assert configs_b[0].values == [conf_b / "values.yaml"]
    assert configs_a[0].values != configs_b[0].values


def test_values_empty_when_not_specified(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[app]]
        type = "helm"
        namespace = "default"
        chart = "./charts/myapp"
        name = "myapp"
        """,
    )

    configs = load_configs(conf_dir)
    assert configs[0].values == []


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def test_validate_config_missing_values_file(tmp_path: Path) -> None:
    config = ChartConfig(
        name="myapp",
        namespace="default",
        chart="./charts/myapp",
        repo=None,
        version=None,
        values=[tmp_path / "nonexistent.yaml"],
        release=None,
    )
    with pytest.raises(ValueError, match="Values file not found"):
        validate_config(config, tmp_path)


def test_validate_config_existing_values_file(tmp_path: Path) -> None:
    values_file = tmp_path / "values.yaml"
    values_file.write_text("key: value\n")

    config = ChartConfig(
        name="myapp",
        namespace="default",
        chart=None,
        repo=None,
        version=None,
        values=[values_file],
        release="myapp",
    )
    validate_config(config, tmp_path)  # should not raise


def test_validate_config_missing_local_chart(tmp_path: Path) -> None:
    config = ChartConfig(
        name="myapp",
        namespace="default",
        chart="./charts/myapp",
        repo=None,
        version=None,
        values=[],
        release=None,
    )
    with pytest.raises(ValueError, match="Local chart path not found"):
        validate_config(config, tmp_path)


# ---------------------------------------------------------------------------
# load_configs
# ---------------------------------------------------------------------------


def test_load_configs_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_configs(tmp_path / "nonexistent")


def test_load_configs_no_toml_files(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    with pytest.raises(FileNotFoundError, match="No TOML files found"):
        load_configs(conf)


def test_load_configs_missing_type_field(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[app]]
        namespace = "default"
        chart = "./charts/myapp"
        name = "myapp"
        """,
    )
    with pytest.raises(ValueError, match="Missing required field 'type'"):
        load_configs(conf)


def test_load_configs_unknown_type(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[app]]
        type = "kustomize"
        namespace = "default"
        name = "myapp"
        """,
    )
    with pytest.raises(ValueError, match="Unknown app type"):
        load_configs(conf)


def test_load_configs_both_release_and_chart_raises(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[app]]
        type = "helm"
        namespace = "default"
        name = "myapp"
        chart = "./charts/myapp"
        release = "myapp"
        """,
    )
    with pytest.raises(ValueError, match="Cannot specify both"):
        load_configs(conf)


def test_load_configs_neither_release_nor_chart_raises(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[app]]
        type = "helm"
        namespace = "default"
        name = "myapp"
        """,
    )
    with pytest.raises(ValueError, match="Must specify either"):
        load_configs(conf)


def test_load_configs_multiple_toml_files(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "a.toml",
        """\
        [[app]]
        type = "helm"
        namespace = "ns-a"
        chart = "./charts/a"
        name = "app-a"
        """,
    )
    write_toml(
        conf,
        "b.toml",
        """\
        [[app]]
        type = "helm"
        namespace = "ns-b"
        chart = "./charts/b"
        name = "app-b"
        """,
    )

    configs = load_configs(conf)
    names = {c.name for c in configs}
    assert names == {"app-a", "app-b"}


# ---------------------------------------------------------------------------
# resolve_configs
# ---------------------------------------------------------------------------


def _make_helmfile() -> Helmfile:
    return Helmfile(
        repositories=[
            HelmfileRepository(name="myrepo", url="https://charts.example.com")
        ],
        releases=[
            HelmfileRelease(
                name="myapp",
                chart="myrepo/myapp",
                version="1.2.3",
                namespace="default",
            )
        ],
    )


def test_resolve_configs_fills_in_chart_and_repo(tmp_path: Path) -> None:
    config = ChartConfig(
        name="myapp",
        namespace="default",
        chart=None,
        repo=None,
        version=None,
        values=[],
        release="myapp",
    )
    resolved = resolve_configs([config], _make_helmfile())
    assert len(resolved) == 1
    assert resolved[0].chart == "myapp"
    assert resolved[0].repo == "https://charts.example.com"
    assert resolved[0].version == "1.2.3"


def test_resolve_configs_no_helmfile_raises_when_release_present(
    tmp_path: Path,
) -> None:
    config = ChartConfig(
        name="myapp",
        namespace="default",
        chart=None,
        repo=None,
        version=None,
        values=[],
        release="myapp",
    )
    with pytest.raises(ValueError, match="no helmfile.yaml was found"):
        resolve_configs([config], None)


def test_resolve_configs_unknown_release_raises() -> None:
    config = ChartConfig(
        name="unknown",
        namespace="default",
        chart=None,
        repo=None,
        version=None,
        values=[],
        release="unknown",
    )
    with pytest.raises(ValueError, match="not found in helmfile.yaml"):
        resolve_configs([config], _make_helmfile())


def test_resolve_configs_passthrough_for_direct_chart() -> None:
    config = ChartConfig(
        name="myapp",
        namespace="default",
        chart="./charts/myapp",
        repo=None,
        version=None,
        values=[],
        release=None,
    )
    resolved = resolve_configs([config], None)
    assert resolved == [config]
