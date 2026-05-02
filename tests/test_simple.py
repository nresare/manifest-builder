# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for simple manifest generation."""

from pathlib import Path

import yaml

from manifest_builder.config import SimpleConfig
from manifest_builder.generator import generate_manifests
from manifest_builder.simple import SimpleConfigHandler, generate_simple


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_generate_simple_writes_deployment_from_bundled_template(
    tmp_path: Path,
) -> None:
    """Simple generation creates a Deployment and ClusterIP Service."""
    config = SimpleConfig(
        name="idcat",
        namespace="idcat",
        image="registry.example.com/idcat:1.0",
    )

    paths = generate_simple(config, tmp_path / "output")

    assert {path.name for path in paths} == {
        "deployment-idcat.yaml",
        "service-idcat.yaml",
    }
    deployment = _read_yaml(tmp_path / "output" / "idcat" / "deployment-idcat.yaml")
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "idcat"
    assert deployment["metadata"]["namespace"] == "idcat"
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "registry.example.com/idcat:1.0"
    assert container["ports"] == [{"name": "http", "containerPort": 8080}]

    service = _read_yaml(tmp_path / "output" / "idcat" / "service-idcat.yaml")
    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "idcat"
    assert service["metadata"]["namespace"] == "idcat"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"] == {"app": "idcat"}
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 80, "targetPort": "http"}
    ]


def test_generate_simple_writes_configmap_when_config_is_specified(
    tmp_path: Path,
) -> None:
    """Config entries create ConfigMaps and mount them in the Deployment."""
    config_file = tmp_path / "myconfig.toml"
    config_file.write_text("[idcat]\nenabled = true\n")
    config = SimpleConfig(
        name="idcat",
        namespace="idcat",
        image="registry.example.com/idcat:1.0",
        config={"/config/myconfig.toml": config_file},
    )

    paths = generate_simple(config, tmp_path / "output")

    assert {path.name for path in paths} == {
        "deployment-idcat.yaml",
        "service-idcat.yaml",
        "configmap-idcat-config.yaml",
    }
    assert not any(
        path.name.startswith(("certificate-", "gateway-", "httproute-"))
        for path in paths
    )

    configmap = _read_yaml(
        tmp_path / "output" / "idcat" / "configmap-idcat-config.yaml"
    )
    assert configmap["kind"] == "ConfigMap"
    assert configmap["metadata"]["name"] == "idcat-config"
    assert configmap["metadata"]["namespace"] == "idcat"
    assert configmap["data"]["myconfig.toml"] == "[idcat]\nenabled = true\n"

    deployment = _read_yaml(tmp_path / "output" / "idcat" / "deployment-idcat.yaml")
    pod_template = deployment["spec"]["template"]
    assert "checksum/config" in pod_template["metadata"]["annotations"]
    pod_spec = pod_template["spec"]
    assert pod_spec["volumes"] == [
        {"name": "idcat-config", "configMap": {"name": "idcat-config"}}
    ]
    assert pod_spec["containers"][0]["volumeMounts"] == [
        {"name": "idcat-config", "mountPath": "/config"}
    ]


def test_generate_simple_writes_serviceaccount_when_iam_role_is_specified(
    tmp_path: Path,
) -> None:
    """iam_role creates a ServiceAccount and references it from the Deployment."""
    config = SimpleConfig(
        name="idcat",
        namespace="idcat",
        image="registry.example.com/idcat:1.0",
        iam_role=("arn:aws:iam::{{account_id}}:role/{{cluster_name}}-idcat"),
        variables={"account_id": "123456789012", "cluster_name": "berries"},
    )

    paths = generate_simple(config, tmp_path / "output")

    assert {path.name for path in paths} == {
        "deployment-idcat.yaml",
        "service-idcat.yaml",
        "serviceaccount-idcat.yaml",
    }

    serviceaccount = _read_yaml(
        tmp_path / "output" / "idcat" / "serviceaccount-idcat.yaml"
    )
    assert serviceaccount["kind"] == "ServiceAccount"
    assert serviceaccount["metadata"]["name"] == "idcat"
    assert serviceaccount["metadata"]["namespace"] == "idcat"
    assert serviceaccount["metadata"]["annotations"] == {
        "eks.amazonaws.com/role-arn": ("arn:aws:iam::123456789012:role/berries-idcat")
    }

    deployment = _read_yaml(tmp_path / "output" / "idcat" / "deployment-idcat.yaml")
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "idcat"


def test_generate_simple_writes_rolebinding_when_k8s_role_is_specified(
    tmp_path: Path,
) -> None:
    """k8s_role creates a ServiceAccount and binds it to the named Role."""
    config = SimpleConfig(
        name="idcat",
        namespace="idcat",
        image="registry.example.com/idcat:1.0",
        k8s_role="{{role_name}}",
        variables={"role_name": "idcat-reader"},
    )

    paths = generate_simple(config, tmp_path / "output")

    assert {path.name for path in paths} == {
        "deployment-idcat.yaml",
        "service-idcat.yaml",
        "serviceaccount-idcat.yaml",
        "rolebinding-idcat-idcat-reader.yaml",
    }

    serviceaccount = _read_yaml(
        tmp_path / "output" / "idcat" / "serviceaccount-idcat.yaml"
    )
    assert serviceaccount["kind"] == "ServiceAccount"
    assert serviceaccount["metadata"]["name"] == "idcat"
    assert serviceaccount["metadata"]["namespace"] == "idcat"
    assert "annotations" not in serviceaccount["metadata"]

    rolebinding = _read_yaml(
        tmp_path / "output" / "idcat" / "rolebinding-idcat-idcat-reader.yaml"
    )
    assert rolebinding["kind"] == "RoleBinding"
    assert rolebinding["metadata"]["name"] == "idcat-idcat-reader"
    assert rolebinding["metadata"]["namespace"] == "idcat"
    assert rolebinding["subjects"] == [
        {"kind": "ServiceAccount", "name": "idcat", "namespace": "idcat"}
    ]
    assert rolebinding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": "idcat-reader",
    }

    deployment = _read_yaml(tmp_path / "output" / "idcat" / "deployment-idcat.yaml")
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == "idcat"


def test_generate_simple_renders_extra_resources_with_variables(
    tmp_path: Path,
) -> None:
    """Extra resource manifests are rendered and namespace defaults are applied."""
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    (extra_dir / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{k8s_name}}-settings\n"
        "data:\n  domain: {{domain}}\n"
    )
    (extra_dir / "storageclass.yaml").write_text(
        "apiVersion: storage.k8s.io/v1\nkind: StorageClass\nmetadata:\n"
        "  name: {{k8s_name}}-storage\nprovisioner: example.com/storage\n"
    )
    config = SimpleConfig(
        name="idcat",
        namespace="idcat",
        image="registry.example.com/idcat:1.0",
        variables={"domain": "example.com"},
        extra_resources=extra_dir,
    )

    paths = generate_simple(config, tmp_path / "output")

    assert "configmap-idcat-settings.yaml" in {path.name for path in paths}
    configmap = _read_yaml(
        tmp_path / "output" / "idcat" / "configmap-idcat-settings.yaml"
    )
    assert configmap["metadata"]["namespace"] == "idcat"
    assert configmap["data"]["domain"] == "example.com"

    storageclass = _read_yaml(
        tmp_path / "output" / "cluster" / "storageclass-idcat-storage.yaml"
    )
    assert storageclass["kind"] == "StorageClass"
    assert "namespace" not in storageclass["metadata"]


def test_generate_manifests_with_simple_config(tmp_path: Path) -> None:
    """generate_manifests dispatches to simple generation."""
    config = SimpleConfig(
        name="idcat",
        namespace="idcat",
        image="registry.example.com/idcat:1.0",
    )

    paths = generate_manifests(
        [SimpleConfigHandler([config])],
        tmp_path / "output",
        repo_root=tmp_path,
    )

    assert {path.name for path in paths} == {
        "deployment-idcat.yaml",
        "service-idcat.yaml",
        "namespace-idcat.yaml",
    }
