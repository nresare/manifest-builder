# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for manifest writing and stale file removal."""

from pathlib import Path

from manifest_builder.generator import strip_helm_metadata, write_manifests

NAMESPACED_YAML = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
  namespace: production
spec: {}
"""

CLUSTER_SCOPED_YAML = """\
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: my-role
rules: []
"""

MULTI_DOC_YAML = NAMESPACED_YAML + "---\n" + CLUSTER_SCOPED_YAML


def test_write_manifests_namespaced_resource(tmp_path: Path) -> None:
    paths = write_manifests(NAMESPACED_YAML, tmp_path, "default")

    assert len(paths) == 1
    (path,) = paths
    # namespace from metadata overrides the passed-in namespace
    assert path == tmp_path / "production" / "deployment-myapp.yaml"
    assert path.exists()


def test_write_manifests_uses_chart_namespace_as_fallback(tmp_path: Path) -> None:
    yaml_without_ns = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config
data: {}
"""
    paths = write_manifests(yaml_without_ns, tmp_path, "fallback-ns")
    (path,) = paths
    assert path.parent.name == "fallback-ns"


def test_write_manifests_cluster_scoped_resource(tmp_path: Path) -> None:
    paths = write_manifests(CLUSTER_SCOPED_YAML, tmp_path, "default")

    assert len(paths) == 1
    (path,) = paths
    assert path == tmp_path / "cluster" / "clusterrole-my-role.yaml"
    assert path.exists()


def test_write_manifests_multi_document(tmp_path: Path) -> None:
    paths = write_manifests(MULTI_DOC_YAML, tmp_path, "default")
    assert len(paths) == 2
    filenames = {p.name for p in paths}
    assert filenames == {"deployment-myapp.yaml", "clusterrole-my-role.yaml"}


def test_write_manifests_skips_empty_documents(tmp_path: Path) -> None:
    yaml_with_empty = "---\n" + NAMESPACED_YAML + "---\n"
    paths = write_manifests(yaml_with_empty, tmp_path, "default")
    assert len(paths) == 1


def test_strip_helm_metadata_removes_helm_labels() -> None:
    doc = {
        "metadata": {
            "labels": {
                "app": "myapp",
                "helm.sh/chart": "mychart-1.0.0",
                "app.kubernetes.io/managed-by": "Helm",
            }
        }
    }
    strip_helm_metadata(doc)
    assert doc["metadata"]["labels"] == {"app": "myapp"}


def test_strip_helm_metadata_removes_helm_annotations() -> None:
    doc = {
        "metadata": {
            "annotations": {
                "helm.sh/hook": "post-install",
                "helm.sh/hook-weight": "1",
                "custom.io/keep": "yes",
            }
        }
    }
    strip_helm_metadata(doc)
    assert doc["metadata"]["annotations"] == {"custom.io/keep": "yes"}


def test_strip_helm_metadata_removes_empty_dicts() -> None:
    doc = {
        "metadata": {
            "labels": {"helm.sh/chart": "mychart-1.0.0"},
            "annotations": {"helm.sh/hook": "post-install"},
        }
    }
    strip_helm_metadata(doc)
    assert "labels" not in doc["metadata"]
    assert "annotations" not in doc["metadata"]


def test_strip_helm_metadata_strips_pod_template() -> None:
    doc = {
        "metadata": {"labels": {"helm.sh/chart": "mychart-1.0.0", "app": "myapp"}},
        "spec": {
            "template": {
                "metadata": {
                    "labels": {"helm.sh/chart": "mychart-1.0.0", "app": "myapp"},
                    "annotations": {"helm.sh/hook": "post-install"},
                }
            }
        },
    }
    strip_helm_metadata(doc)
    assert doc["metadata"]["labels"] == {"app": "myapp"}
    assert doc["spec"]["template"]["metadata"]["labels"] == {"app": "myapp"}
    assert "annotations" not in doc["spec"]["template"]["metadata"]


def test_strip_helm_metadata_preserves_non_helm_managed_by() -> None:
    doc = {
        "metadata": {
            "labels": {"app.kubernetes.io/managed-by": "ArgoCD", "app": "myapp"}
        }
    }
    strip_helm_metadata(doc)
    assert doc["metadata"]["labels"] == {
        "app.kubernetes.io/managed-by": "ArgoCD",
        "app": "myapp",
    }


def test_write_manifests_returns_paths_for_stale_file_removal(tmp_path: Path) -> None:
    stale = tmp_path / "default" / "configmap-old.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale content\n")

    paths = write_manifests(NAMESPACED_YAML, tmp_path, "default")

    # stale file is NOT in the returned set
    assert stale not in paths
    # new file IS in the returned set
    assert any(p.name == "deployment-myapp.yaml" for p in paths)
