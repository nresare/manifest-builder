# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for simple manifest generation."""

from pathlib import Path

import yaml

from manifest_builder.config import SimpleConfig
from manifest_builder.simple import generate_simple


def _make_config(
    tmp_path: Path,
    copy_from: Path,
    config: dict[str, Path] | None = None,
    name: str = "acme-dns",
    namespace: str = "acme-dns",
) -> SimpleConfig:
    return SimpleConfig(
        name=name,
        namespace=namespace,
        copy_from=copy_from,
        config=config,
    )


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Manifest copying
# ---------------------------------------------------------------------------


def test_generate_simple_copies_manifests(tmp_path: Path) -> None:
    """YAML files in copy-from directory are written to output."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    (manifests_dir / "deployment.yaml").write_text(
        """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: acme-dns
  namespace: acme-dns
spec:
  replicas: 1
"""
    )

    output_dir = tmp_path / "output"
    config = _make_config(tmp_path, manifests_dir)
    paths = generate_simple(config, output_dir)

    assert len(paths) == 1
    out = _read_yaml(next(iter(paths)))
    assert out["kind"] == "Deployment"
    assert out["metadata"]["name"] == "acme-dns"


def test_generate_simple_injects_namespace_when_missing(tmp_path: Path) -> None:
    """Namespaced resources without a namespace get the configured namespace."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    (manifests_dir / "service.yaml").write_text(
        """\
apiVersion: v1
kind: Service
metadata:
  name: acme-dns
spec:
  ports:
    - port: 53
"""
    )

    output_dir = tmp_path / "output"
    config = _make_config(tmp_path, manifests_dir)
    generate_simple(config, output_dir)

    out_file = output_dir / "acme-dns" / "service-acme-dns.yaml"
    assert out_file.exists()
    out = _read_yaml(out_file)
    assert out["metadata"]["namespace"] == "acme-dns"


def test_generate_simple_preserves_existing_namespace(tmp_path: Path) -> None:
    """Resources that already have a namespace are not modified."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    (manifests_dir / "service.yaml").write_text(
        """\
apiVersion: v1
kind: Service
metadata:
  name: my-svc
  namespace: other-ns
spec:
  ports:
    - port: 80
"""
    )

    output_dir = tmp_path / "output"
    config = _make_config(tmp_path, manifests_dir)
    generate_simple(config, output_dir)

    out_file = output_dir / "other-ns" / "service-my-svc.yaml"
    assert out_file.exists()
    out = _read_yaml(out_file)
    assert out["metadata"]["namespace"] == "other-ns"


def test_generate_simple_cluster_scoped_resources_no_namespace(tmp_path: Path) -> None:
    """Cluster-scoped resources (e.g. ClusterRole) do not get a namespace."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    (manifests_dir / "clusterrole.yaml").write_text(
        """\
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: acme-dns-role
rules: []
"""
    )

    output_dir = tmp_path / "output"
    config = _make_config(tmp_path, manifests_dir)
    generate_simple(config, output_dir)

    out_file = output_dir / "cluster" / "clusterrole-acme-dns-role.yaml"
    assert out_file.exists()
    out = _read_yaml(out_file)
    assert "namespace" not in out.get("metadata", {})


# ---------------------------------------------------------------------------
# ConfigMap generation
# ---------------------------------------------------------------------------


def test_generate_simple_creates_configmap_from_config(tmp_path: Path) -> None:
    """A ConfigMap is created from the files listed in the config table."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    cfg_file = tmp_path / "app.cfg"
    cfg_file.write_text("[dns]\nport = 53\n")

    output_dir = tmp_path / "output"
    config = _make_config(
        tmp_path,
        manifests_dir,
        config={"/config/app.cfg": cfg_file},
    )
    generate_simple(config, output_dir)

    cm_file = output_dir / "acme-dns" / "configmap-acme-dns-config.yaml"
    assert cm_file.exists()
    cm = _read_yaml(cm_file)
    assert cm["kind"] == "ConfigMap"
    assert cm["metadata"]["name"] == "acme-dns-config"
    assert cm["metadata"]["namespace"] == "acme-dns"
    assert "app.cfg" in cm["data"]
    assert cm["data"]["app.cfg"] == "[dns]\nport = 53\n"


def test_generate_simple_configmap_key_is_filename(tmp_path: Path) -> None:
    """The ConfigMap key is the last component of the container path."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    cfg_file = tmp_path / "config.cfg"
    cfg_file.write_text("setting=true\n")

    output_dir = tmp_path / "output"
    config = _make_config(
        tmp_path,
        manifests_dir,
        config={"/config/config.cfg": cfg_file},
    )
    generate_simple(config, output_dir)

    cm_file = output_dir / "acme-dns" / "configmap-acme-dns-config.yaml"
    cm = _read_yaml(cm_file)
    assert "config.cfg" in cm["data"]


def test_generate_simple_no_config_no_configmap(tmp_path: Path) -> None:
    """When no config is specified, no ConfigMap is created."""
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    (manifests_dir / "service.yaml").write_text(
        """\
apiVersion: v1
kind: Service
metadata:
  name: acme-dns
  namespace: acme-dns
spec:
  ports:
    - port: 53
"""
    )

    output_dir = tmp_path / "output"
    config = _make_config(tmp_path, manifests_dir)
    paths = generate_simple(config, output_dir)

    configmaps = [p for p in paths if "configmap" in p.name]
    assert configmaps == []
