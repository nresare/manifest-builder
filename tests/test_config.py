# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for configuration parsing and validation."""

import textwrap
from pathlib import Path

import pytest

from manifest_builder.config import (
    DEFAULT_REPLICA_COUNT,
    ChartConfig,
    CopyConfig,
    ManifestConfig,
    ManifestConfigs,
    WebsiteConfig,
    load_configs,
    load_images,
    load_owned_namespaces,
    resolve_configs,
    validate_config,
)
from manifest_builder.helmfile import Helmfile, HelmfileRelease, HelmfileRepository


def write_toml(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(content))
    return path


def only_config(configs: ManifestConfigs) -> ManifestConfig:
    (config,) = configs.all_configs()
    return config


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
        [[helm]]
        namespace = "default"
        chart = "./charts/myapp"
        name = "myapp"
        values = ["myapp/values.yaml"]
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
    assert isinstance(config, ChartConfig)
    assert config.values == [conf_dir / "myapp/values.yaml"]


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
            [[helm]]
            namespace = "default"
            chart = "./charts/myapp"
            name = "myapp"
            values = ["values.yaml"]
            """,
        )

    configs_a = load_configs(conf_a)
    configs_b = load_configs(conf_b)

    config_a = only_config(configs_a)
    config_b = only_config(configs_b)
    assert isinstance(config_a, ChartConfig)
    assert isinstance(config_b, ChartConfig)
    assert config_a.values == [conf_a / "values.yaml"]
    assert config_b.values == [conf_b / "values.yaml"]
    assert config_a.values != config_b.values


def test_values_empty_when_not_specified(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[helm]]
        namespace = "default"
        chart = "./charts/myapp"
        name = "myapp"
        """,
    )

    configs = load_configs(conf_dir)
    config = only_config(configs)
    assert isinstance(config, ChartConfig)
    assert config.values == []


def test_variables_are_loaded_for_helm_configs(tmp_path: Path) -> None:
    """Top-level variables should be attached to helm configs from the same TOML file."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [variables]
        domain = "example.com"
        replica_count = 3
        use_tls = true

        [[helm]]
        namespace = "default"
        chart = "./charts/myapp"
        name = "myapp"
        values = ["values.yaml"]
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
    assert isinstance(config, ChartConfig)
    assert config.variables == {
        "domain": "example.com",
        "replica_count": 3,
        "use_tls": True,
    }


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
        description = "this file has no [[helm]], [[website]], or [[copy]]"
        """,
    )
    with pytest.raises(ValueError, match="No \\[\\[helm\\]\\].*\\[\\[copy\\]\\]"):
        load_configs(conf)


def test_load_configs_simple_table_is_not_recognized(tmp_path: Path) -> None:
    """The copy config item is named [[copy]], not [[simple]]."""
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[simple]]
        namespace = "acme-dns"
        source = "manifests"
        """,
    )
    with pytest.raises(ValueError, match="No \\[\\[helm\\]\\].*\\[\\[copy\\]\\]"):
        load_configs(conf)


def test_load_configs_both_release_and_chart_raises(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[helm]]
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
        [[helm]]
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
        [[helm]]
        namespace = "ns-a"
        chart = "./charts/a"
        name = "app-a"
        """,
    )
    write_toml(
        conf,
        "b.toml",
        """\
        [[helm]]
        namespace = "ns-b"
        chart = "./charts/b"
        name = "app-b"
        """,
    )

    configs = load_configs(conf)
    names = {c.name for c in configs.all_configs()}
    assert names == {"app-a", "app-b"}


def test_load_configs_mixed_helms_and_websites(tmp_path: Path) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    write_toml(
        conf,
        "config.toml",
        """\
        [[helm]]
        namespace = "default"
        chart = "./charts/myapp"
        name = "my-helm-app"

        [[website]]
        name = "my-website"
        namespace = "web"
        """,
    )

    configs = load_configs(conf)
    assert len(configs) == 2
    assert len(configs.helm) == 1
    assert len(configs.websites) == 1
    assert isinstance(configs.helm[0], ChartConfig)
    assert isinstance(configs.websites[0], WebsiteConfig)


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
    resolved = resolve_configs(ManifestConfigs(helm=[config]), _make_helmfile())
    assert len(resolved) == 1
    resolved_config = resolved.helm[0]
    assert isinstance(resolved_config, ChartConfig)
    assert resolved_config.chart == "myapp"
    assert resolved_config.repo == "https://charts.example.com"
    assert resolved_config.version == "1.2.3"


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
    with pytest.raises(ValueError, match="no releases.yaml was found"):
        resolve_configs(ManifestConfigs(helm=[config]), None)


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
    with pytest.raises(ValueError, match="not found in releases.yaml"):
        resolve_configs(ManifestConfigs(helm=[config]), _make_helmfile())


def test_resolve_configs_oci_repository() -> None:
    """OCI repositories should be resolved to a full OCI URL chart and no repo."""
    helmfile = Helmfile(
        repositories=[
            HelmfileRepository(
                name="envoyproxy",
                url="docker.io/envoyproxy",
                oci=True,
            )
        ],
        releases=[
            HelmfileRelease(
                name="envoy-gateway",
                chart="envoyproxy/gateway-helm",
                version="v1.7.0",
                namespace="default",
            )
        ],
    )
    config = ChartConfig(
        name="envoy-gateway",
        namespace="default",
        chart=None,
        repo=None,
        version=None,
        values=[],
        release="envoy-gateway",
    )
    resolved = resolve_configs(ManifestConfigs(helm=[config]), helmfile)
    assert len(resolved) == 1
    resolved_config = resolved.helm[0]
    assert isinstance(resolved_config, ChartConfig)
    assert resolved_config.chart == "oci://docker.io/envoyproxy/gateway-helm"
    assert resolved_config.repo is None
    assert resolved_config.version == "v1.7.0"


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
        [[website]]
        name = "my-website"
        namespace = "production"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
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
        [[website]]
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
        [[website]]
        name = "my-website"
        namespace = "production"
        hugo-repo = "https://github.com/user/repo"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.hugo_repo == "https://github.com/user/repo"


def test_load_website_config_with_image(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[website]]
        name = "my-website"
        namespace = "production"
        image = "nginx:latest"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.image == "nginx:latest"


def test_load_website_config_with_args_string(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[website]]
        name = "my-website"
        namespace = "production"
        args = "--flag=value"
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.args == "--flag=value"


def test_load_website_config_with_args_list(tmp_path: Path) -> None:
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
        [[website]]
        name = "my-website"
        namespace = "production"
        args = ["--flag1=value1", "--flag2=value2"]
        """,
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
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
        [[website]]
        name = "my-website"
        namespace = "production"
        hugo-repo = "https://github.com/user/repo"
        image = "nginx:latest"
        """,
    )

    with pytest.raises(ValueError, match="Cannot specify both 'hugo-repo' and 'image'"):
        load_configs(conf_dir)


def test_resolve_configs_passes_website_config_through() -> None:
    config = WebsiteConfig(
        name="my-website",
        namespace="default",
    )
    resolved = resolve_configs(ManifestConfigs(websites=[config]), None)
    assert resolved.websites == [config]


def test_resolve_configs_passthrough_for_direct_chart() -> None:
    config = ChartConfig(
        name="myapp",
        namespace="default",
        chart="./charts/myapp",
        repo=None,
        version=None,
        values=[],
        release=None,
        extra_resources=None,
    )
    resolved = resolve_configs(ManifestConfigs(helm=[config]), None)
    assert resolved.helm == [config]


def test_load_website_config_with_config(tmp_path: Path) -> None:
    """Website config can specify config with local paths."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()

    # Create config files in the conf directory (not with .toml extension to avoid glob)
    config_file = conf_dir / "app.conf"
    config_file.write_text("[app]\nkey = value\n")

    write_toml(
        conf_dir,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
config = { "/config/app.conf" = "app.conf" }
""",
    )

    configs = load_configs(conf_dir)
    assert len(configs) == 1
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.config is not None
    assert config.config["/config/app.conf"] == conf_dir / "app.conf"


def test_load_website_config_multiple_config(tmp_path: Path) -> None:
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
[[website]]
name = "my-app"
namespace = "default"
config = { "/config/app.conf" = "app.conf", "/etc/db.yaml" = "db.yaml" }
""",
    )

    configs = load_configs(conf_dir)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.config is not None
    assert len(config.config) == 2
    assert config.config["/config/app.conf"] == conf_dir / "app.conf"
    assert config.config["/etc/db.yaml"] == conf_dir / "db.yaml"


def test_validate_config_missing_config_file(tmp_path: Path) -> None:
    """Validation should fail if a referenced config file doesn't exist."""
    config = WebsiteConfig(
        name="my-app",
        namespace="default",
        config={"/config/app.toml": tmp_path / "nonexistent.toml"},
    )
    with pytest.raises(ValueError, match="Config file not found"):
        validate_config(config, tmp_path)


def test_load_website_config_with_extra_hostnames_string(tmp_path: Path) -> None:
    """Website config can specify extra_hostnames as a string."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
extra-hostnames = "www.example.com"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.extra_hostnames == "www.example.com"


def test_load_website_config_with_extra_hostnames_list(tmp_path: Path) -> None:
    """Website config can specify extra_hostnames as a list."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
extra-hostnames = ["www.example.com", "example.cdn.com"]
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.extra_hostnames == ["www.example.com", "example.cdn.com"]


def test_load_website_config_with_external_secrets_list(tmp_path: Path) -> None:
    """Website config can specify external_secrets as a list of mount paths."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
image = "nginx:latest"
external-secrets = ["/email-password", "/db/credentials"]
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.external_secrets == ["/email-password", "/db/credentials"]


def test_load_website_config_with_external_secrets_string(tmp_path: Path) -> None:
    """Website config can specify external_secrets as a single string (normalized to list)."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
image = "nginx:latest"
external-secrets = "/api-key"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.external_secrets == ["/api-key"]


def test_load_website_config_with_custom_token_audience(tmp_path: Path) -> None:
    """Website config can specify a custom audience for a projected token."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
image = "nginx:latest"
custom-token-audience = "vault"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.custom_token_audience == "vault"


def test_load_website_config_with_replicas(tmp_path: Path) -> None:
    """Website config can specify replicas for the Deployment."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
image = "nginx:latest"
replicas = 5
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.replicas == 5


def test_load_website_config_replicas_single(tmp_path: Path) -> None:
    """Website config can specify replicas=1."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
image = "nginx:latest"
replicas = 1
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.replicas == 1


def test_load_website_config_replicas_not_specified(tmp_path: Path) -> None:
    """Website config without replicas should default to DEFAULT_REPLICA_COUNT."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[website]]
name = "my-app"
namespace = "default"
image = "nginx:latest"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, WebsiteConfig)
    assert config.replicas == DEFAULT_REPLICA_COUNT


def test_load_chart_config_with_extra_resources(tmp_path: Path) -> None:
    """Chart config can specify a directory with extra YAML resources."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    resources_dir = conf_dir / "resources"
    resources_dir.mkdir()

    write_toml(
        conf_dir,
        "config.toml",
        """\
[[helm]]
name = "my-chart"
namespace = "default"
chart = "./charts/myapp"
extra-resources = "resources"
""",
    )

    configs = load_configs(conf_dir)
    config = only_config(configs)
    assert isinstance(config, ChartConfig)
    assert config.extra_resources == resources_dir


def test_validate_config_chart_extra_resources_missing_directory(
    tmp_path: Path,
) -> None:
    """Validation should fail if extra_resources directory doesn't exist."""
    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="./charts/myapp",
        repo=None,
        version=None,
        values=[],
        release=None,
        extra_resources=tmp_path / "nonexistent",
    )
    with pytest.raises(ValueError, match="Extra resources directory not found"):
        validate_config(config, tmp_path)


def test_validate_config_chart_extra_resources_not_a_directory(tmp_path: Path) -> None:
    """Validation should fail if extra_resources path is not a directory."""
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("content")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="./charts/myapp",
        repo=None,
        version=None,
        values=[],
        release=None,
        extra_resources=not_a_dir,
    )
    with pytest.raises(ValueError, match="Extra resources path is not a directory"):
        validate_config(config, tmp_path)


def test_load_chart_config_with_init(tmp_path: Path) -> None:
    """Chart config can specify an init script to inject as initContainer."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    script_file = conf_dir / "setup.sh"
    script_file.write_text("#!/bin/sh\necho 'initializing'")

    write_toml(
        conf_dir,
        "config.toml",
        """\
[[helm]]
name = "my-chart"
namespace = "default"
chart = "./charts/myapp"
init = "setup.sh"
""",
    )

    configs = load_configs(conf_dir)
    config = only_config(configs)
    assert isinstance(config, ChartConfig)
    assert config.init == script_file


def test_validate_config_chart_init_missing_file(tmp_path: Path) -> None:
    """Validation should fail if init script file doesn't exist."""
    # Create the chart directory so that check passes
    chart_dir = tmp_path / "charts" / "myapp"
    chart_dir.mkdir(parents=True)

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="./charts/myapp",
        repo=None,
        version=None,
        values=[],
        release=None,
        init=tmp_path / "nonexistent.sh",
    )
    with pytest.raises(ValueError, match="init script not found"):
        validate_config(config, tmp_path)


# ---------------------------------------------------------------------------
# Copy config item parsing
# ---------------------------------------------------------------------------


def test_load_copy_config_basic(tmp_path: Path) -> None:
    """Basic [[copy]] entry with namespace and source is parsed correctly."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[copy]]
namespace = "acme-dns"
source = "manifests"
""",
    )

    configs = load_configs(tmp_path)
    assert len(configs) == 1
    config = only_config(configs)
    assert isinstance(config, CopyConfig)
    assert config.namespace == "acme-dns"
    assert config.source == tmp_path / "manifests"
    assert config.config is None


def test_load_copy_config_name_defaults_to_namespace(tmp_path: Path) -> None:
    """When name is omitted, it defaults to the namespace value."""
    (tmp_path / "manifests").mkdir()
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[copy]]
namespace = "acme-dns"
source = "manifests"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, CopyConfig)
    assert config.name == "acme-dns"


def test_load_copy_config_explicit_name(tmp_path: Path) -> None:
    """An explicit name field overrides the namespace default."""
    (tmp_path / "manifests").mkdir()
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[copy]]
name = "my-app"
namespace = "acme-dns"
source = "manifests"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, CopyConfig)
    assert config.name == "my-app"


def test_load_copy_config_missing_namespace(tmp_path: Path) -> None:
    """Missing namespace field raises ValueError."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[copy]]
source = "manifests"
""",
    )
    with pytest.raises(ValueError, match="Missing required field 'namespace'"):
        load_configs(tmp_path)


def test_load_copy_config_missing_source(tmp_path: Path) -> None:
    """Missing source field raises ValueError."""
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[copy]]
namespace = "acme-dns"
""",
    )
    with pytest.raises(ValueError, match="Missing required field 'source'"):
        load_configs(tmp_path)


def test_load_copy_config_source_resolved_relative_to_toml(
    tmp_path: Path,
) -> None:
    """source path is resolved relative to the TOML file's directory."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "manifests").mkdir()
    write_toml(
        conf_dir,
        "config.toml",
        """\
[[copy]]
namespace = "acme-dns"
source = "manifests"
""",
    )

    configs = load_configs(conf_dir)
    config = only_config(configs)
    assert isinstance(config, CopyConfig)
    assert config.source == conf_dir / "manifests"


def test_load_copy_config_with_config_dict(tmp_path: Path) -> None:
    """[copy.config] inline table is parsed into a dict of resolved paths."""
    (tmp_path / "manifests").mkdir()
    (tmp_path / "app.cfg").write_text("key=value")
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[copy]]
namespace = "acme-dns"
source = "manifests"
[copy.config]
"/config/app.cfg" = "app.cfg"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, CopyConfig)
    assert config.config is not None
    assert config.config["/config/app.cfg"] == tmp_path / "app.cfg"


def test_load_copy_config_with_config_array_of_tables(tmp_path: Path) -> None:
    """[[copy.config]] array-of-tables syntax is merged into a single dict."""
    (tmp_path / "manifests").mkdir()
    write_toml(
        tmp_path,
        "config.toml",
        """\
[[copy]]
namespace = "acme-dns"
source = "manifests"
[[copy.config]]
"/config/app.cfg" = "app.cfg"
[[copy.config]]
"/config/other.cfg" = "other.cfg"
""",
    )

    configs = load_configs(tmp_path)
    config = only_config(configs)
    assert isinstance(config, CopyConfig)
    assert config.config is not None
    assert set(config.config.keys()) == {"/config/app.cfg", "/config/other.cfg"}
    assert config.config["/config/app.cfg"] == tmp_path / "app.cfg"
    assert config.config["/config/other.cfg"] == tmp_path / "other.cfg"


def test_validate_copy_config_missing_source_dir(tmp_path: Path) -> None:
    """Validation fails if source directory does not exist."""
    config = CopyConfig(
        name="acme-dns",
        namespace="acme-dns",
        source=tmp_path / "nonexistent",
    )
    with pytest.raises(ValueError, match="source directory not found"):
        validate_config(config, tmp_path)


def test_validate_copy_config_source_not_a_directory(tmp_path: Path) -> None:
    """Validation fails if source path is a file, not a directory."""
    not_a_dir = tmp_path / "file.yaml"
    not_a_dir.write_text("content")
    config = CopyConfig(
        name="acme-dns",
        namespace="acme-dns",
        source=not_a_dir,
    )
    with pytest.raises(ValueError, match="source path is not a directory"):
        validate_config(config, tmp_path)


def test_validate_copy_config_missing_config_file(tmp_path: Path) -> None:
    """Validation fails if a referenced config file does not exist."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    config = CopyConfig(
        name="acme-dns",
        namespace="acme-dns",
        source=manifests_dir,
        config={"/config/app.cfg": tmp_path / "nonexistent.cfg"},
    )
    with pytest.raises(ValueError, match="Config file not found"):
        validate_config(config, tmp_path)


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------


def test_load_images_returns_image_dict(tmp_path: Path) -> None:
    """load_images should return dict mapping variable names to image references."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "images.toml").write_text(
        textwrap.dedent(
            """\
            [git]
            repo = "alpine/git"
            version = "2.47.2"

            [hugo]
            repo = "floryn90/hugo"
            version = "0.155.3-alpine"

            [static-web-server]
            repo = "ghcr.io/static-web-server/static-web-server"
            version = "2.36.1"
            """
        )
    )

    images = load_images(conf_dir)

    assert images == {
        "git_image": "alpine/git:2.47.2",
        "hugo_image": "floryn90/hugo:0.155.3-alpine",
        "static_web_server_image": "ghcr.io/static-web-server/static-web-server:2.36.1",
    }


def test_load_images_returns_empty_dict_when_missing(tmp_path: Path) -> None:
    """load_images should return an empty dict when images.toml is missing."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()

    assert load_images(conf_dir) == {}


def test_load_images_converts_hyphens_to_underscores(tmp_path: Path) -> None:
    """load_images should convert hyphenated keys to underscored variable names."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "images.toml").write_text(
        textwrap.dedent(
            """\
            [my-custom-image]
            repo = "example.com/my-image"
            version = "1.0.0"
            """
        )
    )

    images = load_images(conf_dir)

    assert "my_custom_image_image" in images
    assert images["my_custom_image_image"] == "example.com/my-image:1.0.0"


def test_load_images_raises_on_empty_file(tmp_path: Path) -> None:
    """load_images should raise ValueError if images.toml is empty."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "images.toml").write_text("")

    with pytest.raises(ValueError, match="images.toml is empty"):
        load_images(conf_dir)


def test_load_images_raises_on_invalid_entry(tmp_path: Path) -> None:
    """load_images should raise ValueError if an image entry is missing required fields."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "images.toml").write_text(
        textwrap.dedent(
            """\
            [git]
            repo = "alpine/git"
            """
        )
    )

    with pytest.raises(ValueError, match="must have 'repo' and 'version' fields"):
        load_images(conf_dir)


# ---------------------------------------------------------------------------
# load_owned_namespaces
# ---------------------------------------------------------------------------


def test_load_owned_namespaces_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    """No owners directory means no namespaces are owned by others."""
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()

    assert load_owned_namespaces(conf_dir) == set()


def test_load_owned_namespaces_single_namespace_key(tmp_path: Path) -> None:
    """A 'namespace' string adds one namespace to the owned set."""
    owners_dir = tmp_path / "conf" / "owners"
    owners_dir.mkdir(parents=True)
    (owners_dir / "team-a.toml").write_text('namespace = "team-a"\n')

    assert load_owned_namespaces(tmp_path / "conf") == {"team-a"}


def test_load_owned_namespaces_namespaces_list(tmp_path: Path) -> None:
    """A 'namespaces' list adds each entry to the owned set."""
    owners_dir = tmp_path / "conf" / "owners"
    owners_dir.mkdir(parents=True)
    (owners_dir / "platform.toml").write_text(
        'namespaces = ["monitoring", "logging"]\n'
    )

    assert load_owned_namespaces(tmp_path / "conf") == {"monitoring", "logging"}


def test_load_owned_namespaces_combines_keys_across_files(tmp_path: Path) -> None:
    """Both keys may appear, possibly in different files, and accumulate."""
    owners_dir = tmp_path / "conf" / "owners"
    owners_dir.mkdir(parents=True)
    (owners_dir / "team-a.toml").write_text(
        'namespace = "team-a"\nnamespaces = ["alpha", "beta"]\n'
    )
    (owners_dir / "team-b.toml").write_text('namespace = "team-b"\n')

    assert load_owned_namespaces(tmp_path / "conf") == {
        "team-a",
        "alpha",
        "beta",
        "team-b",
    }


def test_load_owned_namespaces_rejects_non_string_namespace(tmp_path: Path) -> None:
    """A non-string 'namespace' value is a configuration error."""
    owners_dir = tmp_path / "conf" / "owners"
    owners_dir.mkdir(parents=True)
    (owners_dir / "bad.toml").write_text("namespace = 42\n")

    with pytest.raises(ValueError, match="'namespace' must be a string"):
        load_owned_namespaces(tmp_path / "conf")


def test_load_owned_namespaces_rejects_non_list_namespaces(tmp_path: Path) -> None:
    """A non-list 'namespaces' value is a configuration error."""
    owners_dir = tmp_path / "conf" / "owners"
    owners_dir.mkdir(parents=True)
    (owners_dir / "bad.toml").write_text('namespaces = "not-a-list"\n')

    with pytest.raises(ValueError, match="'namespaces' must be a list of strings"):
        load_owned_namespaces(tmp_path / "conf")


def test_load_owned_namespaces_ignores_non_toml_files(tmp_path: Path) -> None:
    """Files without a .toml suffix in owners/ are ignored."""
    owners_dir = tmp_path / "conf" / "owners"
    owners_dir.mkdir(parents=True)
    (owners_dir / "README.md").write_text("# notes\n")
    (owners_dir / "team-a.toml").write_text('namespace = "team-a"\n')

    assert load_owned_namespaces(tmp_path / "conf") == {"team-a"}
