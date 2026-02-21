# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for configuration parsing and validation."""

import textwrap
from pathlib import Path

import pytest

from manifest_builder.config import (
    ChartConfig,
    WebsiteConfig,
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
        [[helms]]
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
            [[helms]]
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
        [[helms]]
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


def test_load_configs_no_recognized_tables(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [metadata]
        description = "this file has no [[helms]] or [[websites]]"
        """,
    )
    with pytest.raises(ValueError, match="No \\[\\[helms\\]\\] or \\[\\[websites\\]\\]"):
        load_configs(conf)


def test_load_configs_both_release_and_chart_raises(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[helms]]
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
        [[helms]]
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
        [[helms]]
        namespace = "ns-a"
        chart = "./charts/a"
        name = "app-a"
        """,
    )
    write_toml(
        conf,
        "b.toml",
        """\
        [[helms]]
        namespace = "ns-b"
        chart = "./charts/b"
        name = "app-b"
        """,
    )

    configs = load_configs(conf)
    names = {c.name for c in configs}
    assert names == {"app-a", "app-b"}


def test_load_configs_mixed_helms_and_websites(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[helms]]
        namespace = "default"
        chart = "./charts/myapp"
        name = "my-helm-app"

        [[websites]]
        name = "my-website"
        namespace = "web"
        """,
    )

    configs = load_configs(conf)
    assert len(configs) == 2
    assert isinstance(configs[0], ChartConfig)
    assert isinstance(configs[1], WebsiteConfig)


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


# ---------------------------------------------------------------------------
# WebsiteConfig parsing
# ---------------------------------------------------------------------------


def test_load_website_config(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[websites]]
        name = "my-website"
        namespace = "production"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, WebsiteConfig)
    assert config.name == "my-website"
    assert config.namespace == "production"


def test_load_website_config_missing_name_field(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[websites]]
        namespace = "default"
        """,
    )
    with pytest.raises(ValueError, match="Missing required field 'name'"):
        load_configs(conf_dir)


def test_load_website_config_with_hugo_repo(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[websites]]
        name = "my-website"
        namespace = "production"
        hugo_repo = "https://github.com/user/repo"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, WebsiteConfig)
    assert config.hugo_repo == "https://github.com/user/repo"


def test_load_website_config_with_image(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[websites]]
        name = "my-website"
        namespace = "production"
        image = "nginx:latest"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, WebsiteConfig)
    assert config.image == "nginx:latest"


def test_load_website_config_with_args_string(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[websites]]
        name = "my-website"
        namespace = "production"
        args = "--flag=value"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, WebsiteConfig)
    assert config.args == "--flag=value"


def test_load_website_config_with_args_list(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[websites]]
        name = "my-website"
        namespace = "production"
        args = ["--flag1=value1", "--flag2=value2"]
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, WebsiteConfig)
    assert config.args == ["--flag1=value1", "--flag2=value2"]


def test_load_website_config_hugo_repo_and_image_mutually_exclusive(
    tmp_path: Path,
) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[websites]]
        name = "my-website"
        namespace = "production"
        hugo_repo = "https://github.com/user/repo"
        image = "nginx:latest"
        """,
    )

    with pytest.raises(ValueError, match="Cannot specify both 'hugo_repo' and 'image'"):
        load_configs(conf_dir)


def test_resolve_configs_passes_website_config_through() -> None:
    config = WebsiteConfig(
        name="my-website",
        namespace="default",
    )
    resolved = resolve_configs([config], None)
    assert resolved == [config]


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


def test_load_website_config_with_config_files(tmp_path: Path) -> None:
    """Website config can specify config_files with local paths."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()

    # Create config files in the conf directory (not with .toml extension to avoid glob)
    config_file = conf_dir / "app.conf"
    config_file.write_text("[app]\nkey = value\n")

    write_toml(
        conf_dir,
        "config.toml",
        """\
[[websites]]
name = "my-app"
namespace = "default"
config_files = { "/config/app.conf" = "app.conf" }
""",
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = configs[0]
    assert isinstance(config, WebsiteConfig)
    assert config.config_files is not None
    assert config.config_files["/config/app.conf"] == conf_dir / "app.conf"


def test_load_website_config_multiple_config_files(tmp_path: Path) -> None:
    """Website config can specify multiple config files in different directories."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()

    # Create config files (use .conf and .yaml to avoid .toml glob)
    (conf_dir / "app.conf").write_text("app")
    (conf_dir / "db.yaml").write_text("db")

    write_toml(
        conf_dir,
        "config.toml",
        """\
[[websites]]
name = "my-app"
namespace = "default"
config_files = { "/config/app.conf" = "app.conf", "/etc/db.yaml" = "db.yaml" }
""",
    )

    configs = load_configs(conf_dir)
    config = configs[0]
    assert len(config.config_files) == 2
    assert config.config_files["/config/app.conf"] == conf_dir / "app.conf"
    assert config.config_files["/etc/db.yaml"] == conf_dir / "db.yaml"


def test_validate_config_missing_config_file(tmp_path: Path) -> None:
    """Validation should fail if a referenced config file doesn't exist."""
    config = WebsiteConfig(
        name="my-app",
        namespace="default",
        config_files={"/config/app.toml": tmp_path / "nonexistent.toml"},
    )
    with pytest.raises(ValueError, match="Config file not found"):
        validate_config(config, tmp_path)
