# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for Helm manifest generation and writing."""

import logging
from pathlib import Path
from unittest import mock

import pytest
import yaml
from pystache.context import KeyNotFoundError

from manifest_builder.config import ChartConfig, ManifestConfigs
from manifest_builder.generator import (
    HelmConfigHandler,
    _ensure_namespaces,
    _generate_helm_manifests,
    _make_k8s_name,
    generate_manifests,
    strip_helm_metadata,
    write_manifests,
)

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


def test_write_manifests_summarizes_skipped_helm_hooks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Helm hook objects should be summarized at info level and detailed at debug."""
    yaml_with_hooks = """\
apiVersion: batch/v1
kind: Job
metadata:
  name: my-hook
  annotations:
    helm.sh/hook: post-install
spec: {}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: my-other-hook
  annotations:
    helm.sh/hook: pre-install
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config
data: {}
"""
    caplog.set_level(logging.DEBUG, logger="manifest_builder.generator")

    paths = write_manifests(yaml_with_hooks, tmp_path, "default")

    assert len(paths) == 1
    assert "Skipped 2 helm hook objects" in caplog.text
    assert "Skipping Job my-hook (helm.sh/hook=post-install)" in caplog.text
    assert "Skipping ServiceAccount my-other-hook (helm.sh/hook=pre-install)" in (
        caplog.text
    )
    summary_records = [
        record
        for record in caplog.records
        if record.message == "Skipped 2 helm hook objects"
    ]
    detail_records = [
        record for record in caplog.records if record.message.startswith("Skipping ")
    ]
    assert [record.levelno for record in summary_records] == [logging.INFO]
    assert {record.levelno for record in detail_records} == {logging.DEBUG}


def test_generate_manifests_summarizes_chart_cache(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Chart cache hit/miss details should be summarized once per generation run."""
    charts_dir = tmp_path / "charts"
    chart_dir = charts_dir / "my-chart-1.0" / "my-chart"
    chart_dir.mkdir(parents=True)
    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="my-chart",
        repo="https://charts.example.com",
        version="1.0",
        values=[],
        release=None,
    )
    caplog.set_level(logging.INFO, logger="manifest_builder.generator")

    with mock.patch(
        "manifest_builder.generator.run_helm_template", return_value=NAMESPACED_YAML
    ):
        generate_manifests(
            ManifestConfigs(helm=[config]),
            tmp_path / "out",
            repo_root=tmp_path,
            handlers=[HelmConfigHandler()],
            charts_dir=charts_dir,
        )

    assert "Chart cache: 1 hit, 0 misses" in caplog.text


def test_generate_manifests_rejects_config_in_owned_namespace(tmp_path: Path) -> None:
    """Configs targeting an externally-owned namespace must be rejected."""
    config = ChartConfig(
        name="my-chart",
        namespace="team-a",
        chart=str(tmp_path / "chart"),
        repo=None,
        version=None,
        values=[],
        release=None,
    )
    (tmp_path / "chart").mkdir()

    with pytest.raises(ValueError, match="owned by another service"):
        generate_manifests(
            ManifestConfigs(helm=[config]),
            tmp_path / "out",
            repo_root=tmp_path,
            handlers=[HelmConfigHandler()],
            owned_namespaces={"team-a"},
        )


def test_generate_manifests_preserves_files_in_owned_namespace(tmp_path: Path) -> None:
    """Pre-existing files in owned namespace directories must survive cleanup."""
    output_dir = tmp_path / "out"
    owned_file = output_dir / "team-a" / "configmap-foo.yaml"
    owned_file.parent.mkdir(parents=True)
    owned_file.write_text("# owned by team-a\n")

    chart_dir = tmp_path / "charts" / "my-chart-1.0" / "my-chart"
    chart_dir.mkdir(parents=True)
    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="my-chart",
        repo="https://charts.example.com",
        version="1.0",
        values=[],
        release=None,
    )

    with mock.patch(
        "manifest_builder.generator.run_helm_template", return_value=NAMESPACED_YAML
    ):
        written = generate_manifests(
            ManifestConfigs(helm=[config]),
            output_dir,
            repo_root=tmp_path,
            handlers=[HelmConfigHandler()],
            charts_dir=tmp_path / "charts",
            owned_namespaces={"team-a"},
        )

    assert owned_file.exists()
    assert owned_file not in written


def test_generate_manifests_rejects_output_landing_in_owned_namespace(
    tmp_path: Path,
) -> None:
    """A doc whose metadata.namespace targets an owned namespace must fail."""
    chart_dir = tmp_path / "charts" / "my-chart-1.0" / "my-chart"
    chart_dir.mkdir(parents=True)
    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="my-chart",
        repo="https://charts.example.com",
        version="1.0",
        values=[],
        release=None,
    )
    intrusive_yaml = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: foo
  namespace: team-a
data: {}
"""

    with mock.patch(
        "manifest_builder.generator.run_helm_template", return_value=intrusive_yaml
    ):
        with pytest.raises(ValueError, match="owned by another service"):
            generate_manifests(
                ManifestConfigs(helm=[config]),
                tmp_path / "out",
                repo_root=tmp_path,
                handlers=[HelmConfigHandler()],
                charts_dir=tmp_path / "charts",
                owned_namespaces={"team-a"},
            )


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


def test_strip_helm_metadata_handles_null_labels() -> None:
    """strip_helm_metadata should not crash when labels or annotations is null in the YAML."""
    doc = {"metadata": {"labels": None, "annotations": None}}
    strip_helm_metadata(doc)
    assert doc["metadata"]["labels"] is None
    assert doc["metadata"]["annotations"] is None


def test_write_manifests_handles_null_annotations(tmp_path: Path) -> None:
    """Documents emitted by the cilium chart contain `annotations:` with a null value.

    The hook-filter code path reaches `.get("annotations", {}).get("helm.sh/hook")`;
    because `.get(key, default)` only uses the default when the key is absent,
    a null annotations value used to surface as `AttributeError: 'NoneType' object
    has no attribute 'get'`. See cilium 1.19.3 Namespace/cilium-secrets.
    """
    cilium_namespace_yaml = """\
apiVersion: v1
kind: Namespace
metadata:
  name: "cilium-secrets"
  labels:
    app.kubernetes.io/part-of: cilium
  annotations:
"""
    paths = write_manifests(cilium_namespace_yaml, tmp_path, "default")
    assert len(paths) == 1


def test_write_manifests_raises_on_non_dict_annotations(tmp_path: Path) -> None:
    """A YAML document with non-dict annotations should raise a descriptive error."""
    yaml_bad_annotations = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config
  annotations: "not-a-dict"
data: {}
"""
    with pytest.raises(
        TypeError,
        match=(
            r"failed to read annotations on object ConfigMap from mychart, "
            r"item annotations is not a dict"
        ),
    ):
        write_manifests(yaml_bad_annotations, tmp_path, "default", app_name="mychart")


def test_write_manifests_returns_paths_for_stale_file_removal(tmp_path: Path) -> None:
    stale = tmp_path / "default" / "configmap-old.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale content\n")

    paths = write_manifests(NAMESPACED_YAML, tmp_path, "default")

    # stale file is NOT in the returned set
    assert stale not in paths
    # new file IS in the returned set
    assert any(p.name == "deployment-myapp.yaml" for p in paths)


def test_make_k8s_name_valid_domain() -> None:
    """Valid domain names should be converted correctly."""
    assert _make_k8s_name("example.com") == "example-com"
    assert _make_k8s_name("my.example.com") == "my-example-com"
    assert _make_k8s_name("zq.lu") == "zq-lu"


def test_make_k8s_name_valid_alphanumeric() -> None:
    """Valid alphanumeric names with dashes should be preserved."""
    assert _make_k8s_name("my-app") == "my-app"
    assert _make_k8s_name("app123") == "app123"
    assert _make_k8s_name("A-B-C") == "a-b-c"


def test_make_k8s_name_starts_with_dash() -> None:
    """Names starting with a period (resulting in dash) should raise ValueError."""
    with pytest.raises(ValueError, match="must start with an alphanumeric character"):
        _make_k8s_name(".example.com")


def test_make_k8s_name_ends_with_dash() -> None:
    """Names ending with a period (resulting in dash) should raise ValueError."""
    with pytest.raises(ValueError, match="must end with an alphanumeric character"):
        _make_k8s_name("example.com.")


def test_make_k8s_name_exceeds_63_characters() -> None:
    """Names exceeding 63 characters should raise ValueError."""
    long_name = "a" * 50 + "." + "b" * 20  # Will exceed 63 after replacement
    with pytest.raises(ValueError, match="exceeds 63 character limit"):
        _make_k8s_name(long_name)


def test_make_k8s_name_only_periods() -> None:
    """Names consisting only of periods should fail validation."""
    # A single period becomes a single dash, which fails the alphanumeric start/end check
    with pytest.raises(ValueError, match="must start with an alphanumeric character"):
        _make_k8s_name(".")


def test_make_k8s_name_invalid_characters() -> None:
    """Names with invalid characters (after conversion) should raise ValueError."""
    with pytest.raises(ValueError, match="contains invalid characters"):
        _make_k8s_name("my_app")  # underscore is invalid


# ---------------------------------------------------------------------------
# _ensure_namespaces
# ---------------------------------------------------------------------------


def test_ensure_namespaces_creates_namespace_for_each_directory(
    tmp_path: Path,
) -> None:
    """A Namespace manifest is created for each namespace directory."""
    ns_dir = tmp_path / "my-app"
    ns_dir.mkdir()
    (ns_dir / "deployment-my-app.yaml").write_text("placeholder")

    written: dict[Path, str] = {ns_dir / "deployment-my-app.yaml": "my-app"}
    new = _ensure_namespaces(tmp_path, written)

    ns_file = ns_dir / "namespace-my-app.yaml"
    assert ns_file in new
    doc = yaml.safe_load(ns_file.read_text())
    assert doc["kind"] == "Namespace"
    assert doc["metadata"]["name"] == "my-app"


def test_ensure_namespaces_skips_cluster_directory(tmp_path: Path) -> None:
    """The cluster/ directory is not treated as a namespace."""
    cluster_dir = tmp_path / "cluster"
    cluster_dir.mkdir()
    (cluster_dir / "clusterrole-foo.yaml").write_text("placeholder")

    written: dict[Path, str] = {cluster_dir / "clusterrole-foo.yaml": "foo"}
    new = _ensure_namespaces(tmp_path, written)

    assert new == {}


def test_ensure_namespaces_skips_when_namespace_already_written(
    tmp_path: Path,
) -> None:
    """No Namespace is created if one was already written by a generator."""
    ns_dir = tmp_path / "my-app"
    ns_dir.mkdir()
    ns_file = ns_dir / "namespace-my-app.yaml"
    ns_file.write_text("existing")

    written: dict[Path, str] = {ns_file: "my-app"}
    new = _ensure_namespaces(tmp_path, written)

    assert new == {}


def test_ensure_namespaces_skips_when_namespace_in_cluster_dir(
    tmp_path: Path,
) -> None:
    """No Namespace is created if one was written to cluster/ by a generator."""
    ns_dir = tmp_path / "my-app"
    ns_dir.mkdir()
    cluster_ns = tmp_path / "cluster" / "namespace-my-app.yaml"
    cluster_ns.parent.mkdir()
    cluster_ns.write_text("existing")

    written: dict[Path, str] = {cluster_ns: "my-app"}
    new = _ensure_namespaces(tmp_path, written)

    assert new == {}


def test_ensure_namespaces_multiple_namespaces(tmp_path: Path) -> None:
    """Each namespace directory gets its own Namespace manifest."""
    for ns in ("ns-a", "ns-b"):
        (tmp_path / ns).mkdir()

    written: dict[Path, str] = {}
    new = _ensure_namespaces(tmp_path, written)

    assert tmp_path / "ns-a" / "namespace-ns-a.yaml" in new
    assert tmp_path / "ns-b" / "namespace-ns-b.yaml" in new


def test_ensure_namespaces_nonexistent_output_dir(tmp_path: Path) -> None:
    """Returns empty dict when the output directory does not exist yet."""
    new = _ensure_namespaces(tmp_path / "nonexistent", {})
    assert new == {}


# ---------------------------------------------------------------------------
# _generate_helm_manifests CRD handling
# ---------------------------------------------------------------------------


def test_generate_helm_manifests_includes_crds(tmp_path: Path) -> None:
    """CRDs from chart's crds/ directory are included in output."""
    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()

    # Create a fake chart directory with CRDs
    chart_dir = charts_dir / "my-chart"
    chart_dir.mkdir()
    crds_dir = chart_dir / "crds"
    crds_dir.mkdir()

    # Create a CRD YAML file
    crd_yaml = crds_dir / "crd.yaml"
    crd_content = """\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: myresources.example.com
spec:
  group: example.com
  names:
    kind: MyResource
  scope: Namespaced
"""
    crd_yaml.write_text(crd_content)

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="my-chart",
        repo="https://example.com",
        version="1.0.0",
        values=[],
        release=None,
    )

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value="",  # No templated manifests
        ),
    ):
        paths = _generate_helm_manifests(config, output_dir, charts_dir)

    # Verify CRD was written to cluster directory
    crd_output = (
        output_dir / "cluster" / "customresourcedefinition-myresources.example.com.yaml"
    )
    assert crd_output.exists()

    # Verify the content
    written_crd = yaml.safe_load(crd_output.read_text())
    assert written_crd["kind"] == "CustomResourceDefinition"
    assert written_crd["metadata"]["name"] == "myresources.example.com"
    assert crd_output in paths


def test_generate_helm_manifests_no_crds_dir(tmp_path: Path) -> None:
    """When chart has no crds/ directory, only templated manifests are included."""
    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()

    # Create a fake chart directory without CRDs
    chart_dir = charts_dir / "my-chart"
    chart_dir.mkdir()

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="my-chart",
        repo="https://example.com",
        version="1.0.0",
        values=[],
        release=None,
    )

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value="",  # No templated manifests
        ),
    ):
        paths = _generate_helm_manifests(config, output_dir, charts_dir)

    # Verify no cluster directory was created (no CRDs)
    cluster_dir = output_dir / "cluster"
    assert not cluster_dir.exists() or not any(cluster_dir.iterdir())
    assert len(paths) == 0


def test_generate_helm_manifests_crds_with_templated_manifests(tmp_path: Path) -> None:
    """CRDs are combined with templated manifests in output."""
    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()

    # Create a fake chart directory with CRDs
    chart_dir = charts_dir / "my-chart"
    chart_dir.mkdir()
    crds_dir = chart_dir / "crds"
    crds_dir.mkdir()

    # Create a CRD YAML file
    crd_yaml = crds_dir / "crd.yaml"
    crd_yaml.write_text("""\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: myresources.example.com
spec:
  group: example.com
  names:
    kind: MyResource
  scope: Namespaced
""")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="my-chart",
        repo="https://example.com",
        version="1.0.0",
        values=[],
        release=None,
    )

    # Mock helm template to return a deployment
    templated_manifest = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec: {}
"""

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value=templated_manifest,
        ),
    ):
        paths = _generate_helm_manifests(config, output_dir, charts_dir)

    # Verify both CRD and deployment are in output
    crd_output = (
        output_dir / "cluster" / "customresourcedefinition-myresources.example.com.yaml"
    )
    deployment_output = output_dir / "default" / "deployment-my-app.yaml"

    assert crd_output.exists()
    assert deployment_output.exists()
    assert len(paths) == 2


def test_generate_helm_manifests_crds_in_subdirectories(tmp_path: Path) -> None:
    """CRDs in subdirectories of crds/ directory are included recursively."""
    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()

    # Create a fake chart directory with CRDs in subdirectories
    chart_dir = charts_dir / "my-chart"
    chart_dir.mkdir()
    crds_dir = chart_dir / "crds"
    crds_dir.mkdir()

    # Create CRD in subdirectory
    sub_dir = crds_dir / "v1"
    sub_dir.mkdir()
    crd_yaml = sub_dir / "resource.yaml"
    crd_yaml.write_text("""\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: items.example.com
spec:
  group: example.com
  names:
    kind: Item
  scope: Namespaced
""")

    # Create CRD at root of crds/ directory
    root_crd = crds_dir / "root.yaml"
    root_crd.write_text("""\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: roots.example.com
spec:
  group: example.com
  names:
    kind: Root
  scope: Namespaced
""")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart="my-chart",
        repo="https://example.com",
        version="1.0.0",
        values=[],
        release=None,
    )

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value="",
        ),
    ):
        paths = _generate_helm_manifests(config, output_dir, charts_dir)

    # Verify both CRDs are in output (one from root, one from subdirectory)
    root_crd_output = (
        output_dir / "cluster" / "customresourcedefinition-roots.example.com.yaml"
    )
    sub_crd_output = (
        output_dir / "cluster" / "customresourcedefinition-items.example.com.yaml"
    )

    assert root_crd_output.exists()
    assert sub_crd_output.exists()
    assert len(paths) == 2


def test_generate_helm_manifests_init_single_deployment(tmp_path: Path) -> None:
    """init script should be injected as initContainer when exactly one Deployment exists."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()

    script_file = tmp_path / "setup.sh"
    script_file.write_text("mkdir -p /data && chown 65532:65532 /data")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[],
        release=None,
        init=script_file,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    deployment_yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:1.0
        volumeMounts:
        - name: data
          mountPath: /data
      volumes:
      - name: data
        emptyDir: {}
"""

    images = {"alpine_image": "alpine:3.21"}

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value=deployment_yaml,
        ),
    ):
        paths = _generate_helm_manifests(config, output_dir, charts_dir, images=images)

    assert len(paths) == 1
    deployment_file = output_dir / "default" / "deployment-my-app.yaml"
    assert deployment_file.exists()

    doc = yaml.safe_load(deployment_file.read_text())
    assert "initContainers" in doc["spec"]["template"]["spec"]
    init_containers = doc["spec"]["template"]["spec"]["initContainers"]
    assert len(init_containers) == 1
    assert init_containers[0]["name"] == "setup"
    assert init_containers[0]["image"] == "alpine:3.21"
    assert init_containers[0]["command"] == [
        "/bin/sh",
        "-c",
        "mkdir -p /data && chown 65532:65532 /data",
    ]
    assert len(init_containers[0]["volumeMounts"]) == 1
    assert init_containers[0]["volumeMounts"][0]["name"] == "data"


def test_generate_helm_manifests_init_multiple_deployments(tmp_path: Path) -> None:
    """init should fail with ValueError if more than one Deployment exists."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()

    script_file = tmp_path / "setup.sh"
    script_file.write_text("echo 'init'")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[],
        release=None,
        init=script_file,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    two_deployments = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app1
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:1.0
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app2
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:1.0
"""

    images = {"alpine_image": "alpine:3.21"}

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value=two_deployments,
        ),
    ):
        with pytest.raises(
            ValueError, match="init requires exactly one Deployment.*found 2"
        ):
            _generate_helm_manifests(config, output_dir, charts_dir, images=images)


def test_generate_helm_manifests_init_no_deployments(tmp_path: Path) -> None:
    """init should fail with ValueError if no Deployments exist."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()

    script_file = tmp_path / "setup.sh"
    script_file.write_text("echo 'init'")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[],
        release=None,
        init=script_file,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    service_yaml = """\
apiVersion: v1
kind: Service
metadata:
  name: my-service
spec:
  selector:
    app: myapp
"""

    images = {"alpine_image": "alpine:3.21"}

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value=service_yaml,
        ),
    ):
        with pytest.raises(
            ValueError, match="init requires exactly one Deployment.*found 0"
        ):
            _generate_helm_manifests(config, output_dir, charts_dir, images=images)


def test_generate_helm_manifests_init_missing_alpine_image(tmp_path: Path) -> None:
    """init should fail if alpine_image is not in images dict."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()

    script_file = tmp_path / "setup.sh"
    script_file.write_text("echo 'init'")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[],
        release=None,
        init=script_file,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    deployment_yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:1.0
"""

    images = {}  # missing alpine_image

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value=deployment_yaml,
        ),
    ):
        with pytest.raises(
            ValueError,
            match="init requires 'alpine_image' to be defined in images.toml",
        ):
            _generate_helm_manifests(config, output_dir, charts_dir, images=images)


def test_generate_helm_manifests_init_no_volumemounts(tmp_path: Path) -> None:
    """init container should have no volumeMounts if containers have none."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()

    script_file = tmp_path / "setup.sh"
    script_file.write_text("echo 'init'")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[],
        release=None,
        init=script_file,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    deployment_yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:1.0
"""

    images = {"alpine_image": "alpine:3.21"}

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value=deployment_yaml,
        ),
    ):
        _generate_helm_manifests(config, output_dir, charts_dir, images=images)

    deployment_file = output_dir / "default" / "deployment-my-app.yaml"
    doc = yaml.safe_load(deployment_file.read_text())
    init_containers = doc["spec"]["template"]["spec"]["initContainers"]
    assert "volumeMounts" not in init_containers[0]


def test_generate_helm_manifests_no_init(tmp_path: Path) -> None:
    """Without init configured, Deployment should not have initContainers."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[],
        release=None,
        init=None,  # no init script
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    deployment_yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:1.0
"""

    with (
        mock.patch(
            "manifest_builder.generator.pull_chart",
            return_value=chart_dir,
        ),
        mock.patch(
            "manifest_builder.generator.run_helm_template",
            return_value=deployment_yaml,
        ),
    ):
        _generate_helm_manifests(config, output_dir, charts_dir)

    deployment_file = output_dir / "default" / "deployment-my-app.yaml"
    doc = yaml.safe_load(deployment_file.read_text())
    assert "initContainers" not in doc["spec"]["template"]["spec"]


def test_generate_helm_manifests_renders_values_files_with_variables(
    tmp_path: Path,
) -> None:
    """Helm values files should be rendered with config variables before templating."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()
    values_file = tmp_path / "values.yaml"
    values_file.write_text("hostname: {{domain}}\nreplicas: {{replicas}}\n")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[values_file],
        variables={"domain": "example.com", "replicas": 2},
        release=None,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"
    captured_values: dict[str, str] = {}

    def fake_run_helm_template(
        release_name: str,
        chart: str,
        namespace: str,
        values_files: list[Path],
        version: str | None = None,
    ) -> str:
        del release_name, chart, namespace, version
        captured_values["content"] = values_files[0].read_text()
        return ""

    with mock.patch(
        "manifest_builder.generator.run_helm_template",
        side_effect=fake_run_helm_template,
    ):
        paths = _generate_helm_manifests(config, output_dir, charts_dir)

    assert paths == set()
    assert captured_values["content"] == "hostname: example.com\nreplicas: 2\n"


def test_generate_helm_manifests_raises_on_missing_variable_in_values_file(
    tmp_path: Path,
) -> None:
    """Rendering values files should fail when a referenced variable is missing."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()
    values_file = tmp_path / "values.yaml"
    values_file.write_text("hostname: {{domain}}\n")

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[values_file],
        release=None,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    with pytest.raises(KeyNotFoundError):
        _generate_helm_manifests(config, output_dir, charts_dir)


def test_generate_helm_manifests_renders_extra_resources_with_variables(
    tmp_path: Path,
) -> None:
    """Extra resource manifests should be rendered with config variables before parsing."""
    chart_dir = tmp_path / "chart"
    chart_dir.mkdir()
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    (extra_dir / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: my-config\n"
        "data:\n  domain: {{domain}}\n"
    )

    config = ChartConfig(
        name="my-chart",
        namespace="default",
        chart=str(chart_dir),
        repo=None,
        version=None,
        values=[],
        variables={"domain": "example.com"},
        release=None,
        extra_resources=extra_dir,
    )

    output_dir = tmp_path / "output"
    charts_dir = tmp_path / "charts"

    with mock.patch(
        "manifest_builder.generator.run_helm_template",
        return_value="",
    ):
        _generate_helm_manifests(config, output_dir, charts_dir)

    written = list(output_dir.rglob("*.yaml"))
    assert len(written) == 1
    content = written[0].read_text()
    assert "example.com" in content
    assert "{{domain}}" not in content
