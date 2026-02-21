# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for manifest writing and stale file removal."""

from pathlib import Path

import pytest
import yaml

from manifest_builder.config import WebsiteConfig
from manifest_builder.generator import _generate_website, _make_k8s_name, generate_manifests, strip_helm_metadata, write_manifests

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


# ---------------------------------------------------------------------------
# Website app generation
# ---------------------------------------------------------------------------

SIMPLE_DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-website
  namespace: staging
spec: {}
"""


def _make_website_config(tmp_path: Path, patch: str | None = None) -> tuple[WebsiteConfig, Path]:
    """Create a WebsiteConfig for testing, with a temporary templates directory.

    Returns a tuple of (config, templates_dir).
    """
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "deployment.yaml").write_text(SIMPLE_DEPLOYMENT)
    patch_path = None
    if patch is not None:
        patch_file = tmp_path / "patch.py"
        patch_file.write_text(patch)
        patch_path = patch_file
    return (
        WebsiteConfig(
            name="my-website",
            namespace="staging",
            patch=patch_path,
        ),
        templates_dir,
    )


def test_generate_website_writes_yaml_files(tmp_path: Path) -> None:
    config, templates_dir = _make_website_config(tmp_path)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    assert len(paths) == 1
    (path,) = paths
    assert path == tmp_path / "output" / "staging" / "deployment-my-website.yaml"
    assert path.exists()


def test_generate_website_adds_source_comment(tmp_path: Path) -> None:
    config, templates_dir = _make_website_config(tmp_path)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    (path,) = paths
    content = path.read_text()
    assert content.startswith("# Source: my-website\n")


def test_generate_website_calls_patch_function(tmp_path: Path) -> None:
    patch_code = """\
def patch(doc):
    doc.setdefault("metadata", {})["labels"] = {"patched": "true"}
"""
    config, templates_dir = _make_website_config(tmp_path, patch=patch_code)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    (path,) = paths
    doc = yaml.safe_load(path.read_text())
    assert doc["metadata"]["labels"] == {"patched": "true"}


def test_generate_website_patch_return_value_used(tmp_path: Path) -> None:
    patch_code = """\
def patch(doc):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "replaced"}}
"""
    config, templates_dir = _make_website_config(tmp_path, patch=patch_code)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    assert len(paths) == 1
    (path,) = paths
    assert path.name == "configmap-replaced.yaml"


def test_generate_website_multiple_yaml_files(tmp_path: Path) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "deploy.yaml").write_text(SIMPLE_DEPLOYMENT)
    (templates_dir / "service.yaml").write_text(
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: simple-svc\n  namespace: staging\n"
    )
    config = WebsiteConfig(
        name="my-website", namespace="staging", patch=None
    )
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)
    names = {p.name for p in paths}
    assert names == {"deployment-my-website.yaml", "service-simple-svc.yaml"}


def test_generate_website_missing_patch_function_raises(tmp_path: Path) -> None:
    patch_code = "# no patch function here\n"
    config, templates_dir = _make_website_config(tmp_path, patch=patch_code)
    with pytest.raises(ValueError, match="No 'patch' function defined"):
        _generate_website(config, tmp_path / "output", _templates_override=templates_dir)


def test_generate_website_multi_document_template_file(tmp_path: Path) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "resources.yaml").write_text(
        SIMPLE_DEPLOYMENT
        + "---\n"
        + "apiVersion: v1\nkind: Service\nmetadata:\n  name: simple-svc\n  namespace: staging\n"
    )
    config = WebsiteConfig(
        name="my-website", namespace="staging", patch=None
    )
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)
    names = {p.name for p in paths}
    assert names == {"deployment-my-website.yaml", "service-simple-svc.yaml"}


def test_generate_website_empty_templates_dir(tmp_path: Path) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    config = WebsiteConfig(
        name="my-website", namespace="staging", patch=None
    )
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)
    assert paths == set()


def test_generate_website_non_yaml_files_ignored(tmp_path: Path) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "deployment.yaml").write_text(SIMPLE_DEPLOYMENT)
    (templates_dir / "notes.txt").write_text("this should be ignored\n")
    (templates_dir / "script.sh").write_text("#!/bin/sh\n")
    config = WebsiteConfig(
        name="my-website", namespace="staging", patch=None
    )
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)
    assert len(paths) == 1


def test_generate_website_cluster_scoped_resource(tmp_path: Path) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "clusterrole.yaml").write_text(
        "apiVersion: rbac.authorization.k8s.io/v1\n"
        "kind: ClusterRole\n"
        "metadata:\n"
        "  name: my-role\n"
        "rules: []\n"
    )
    config = WebsiteConfig(
        name="my-website", namespace="staging", patch=None
    )
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)
    (path,) = paths
    assert path == tmp_path / "output" / "cluster" / "clusterrole-my-role.yaml"


def test_generate_website_namespace_fallback(tmp_path: Path) -> None:
    """Resources without a namespace in metadata use the config namespace."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: my-config\ndata: {}\n"
    )
    config = WebsiteConfig(
        name="my-website", namespace="fallback-ns", patch=None
    )
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)
    (path,) = paths
    assert path.parent.name == "fallback-ns"


def test_generate_manifests_with_website_config(tmp_path: Path) -> None:
    """generate_manifests dispatches to website app generation without needing helm."""
    config = WebsiteConfig(
        name="zq.lu", namespace="web", patch=None
    )
    output_dir = tmp_path / "output"

    generate_manifests([config], output_dir, repo_root=tmp_path)

    # Check that files were generated from the bundled web templates
    assert output_dir.exists()
    generated_files = list(output_dir.rglob("*.yaml"))
    assert len(generated_files) > 0
    # The k8s_name template variable converts "zq.lu" to "zq-lu"
    assert any("zq-lu" in str(f) for f in generated_files)


def test_generate_manifests_removes_stale_website_files(tmp_path: Path) -> None:
    """Stale files from previous runs are removed after generating website manifests."""
    output_dir = tmp_path / "output"
    stale = output_dir / "web" / "configmap-old.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale: true\n")

    config = WebsiteConfig(
        name="zq.lu", namespace="web", patch=None
    )
    generate_manifests([config], output_dir, repo_root=tmp_path)

    assert not stale.exists()
    # Check that files were generated
    assert any(output_dir.rglob("*.yaml"))


def test_generate_website_file_content_is_valid_yaml(tmp_path: Path) -> None:
    """Each output file must be parseable YAML with the correct structure."""
    config, templates_dir = _make_website_config(tmp_path)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    (path,) = paths
    # skip the leading comment line before parsing
    text = path.read_text()
    doc = yaml.safe_load(text)
    assert doc["kind"] == "Deployment"
    assert doc["metadata"]["name"] == "my-website"


def test_generate_manifests_detects_output_file_conflicts(tmp_path: Path) -> None:
    """generate_manifests should error when different configs generate the same files."""
    templates_dir1 = tmp_path / "templates1"
    templates_dir1.mkdir()
    (templates_dir1 / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\nspec: {}\n"
    )

    templates_dir2 = tmp_path / "templates2"
    templates_dir2.mkdir()
    (templates_dir2 / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\nspec: {}\n"
    )

    config1 = WebsiteConfig(name="app1", namespace="ns1", patch=None)
    config2 = WebsiteConfig(name="app2", namespace="ns1", patch=None)

    output_dir = tmp_path / "output"

    # Mock the templates for both configs to generate the same output files
    import unittest.mock as mock

    with mock.patch(
        "manifest_builder.generator._generate_website",
        side_effect=[
            {tmp_path / "output" / "ns1" / "deployment-app.yaml"},  # config1 generates this
            {tmp_path / "output" / "ns1" / "deployment-app.yaml"},  # config2 also generates this
        ],
    ):
        with pytest.raises(ValueError, match="Configuration conflict"):
            generate_manifests([config1, config2], output_dir, repo_root=tmp_path)


def test_generate_website_provides_k8s_name_variable(tmp_path: Path) -> None:
    """Website templates should have k8s_name available (name with periods replaced by dashes)."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    # Create a template that uses the {{k8s_name}} variable
    (templates_dir / "service.yaml").write_text(
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: {{k8s_name}}\nspec: {}\n"
    )

    config = WebsiteConfig(name="my.example.com", namespace="production", patch=None)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    (path,) = paths
    doc = yaml.safe_load(path.read_text())
    # The {{k8s_name}} should have been rendered with periods replaced by dashes
    assert doc["metadata"]["name"] == "my-example-com"


def test_generate_website_renders_handlebars_templates(tmp_path: Path) -> None:
    """Website templates should be rendered as Handlebars with the website name available."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    # Create a template that uses the {{name}} variable
    (templates_dir / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{name}}\nspec: {}\n"
    )

    config = WebsiteConfig(name="my-app", namespace="production", patch=None)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    (path,) = paths
    doc = yaml.safe_load(path.read_text())
    # The {{name}} should have been rendered to "my-app"
    assert doc["metadata"]["name"] == "my-app"


def test_generate_website_adds_namespace_to_namespaced_resources(tmp_path: Path) -> None:
    """Namespaced resources should get the namespace from the config."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    # Create a Deployment without a namespace
    (templates_dir / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: test-app\nspec: {}\n"
    )
    # Create a ClusterRole (should NOT get namespace)
    (templates_dir / "clusterrole.yaml").write_text(
        "apiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRole\nmetadata:\n  name: test-role\nrules: []\n"
    )

    config = WebsiteConfig(name="test-app", namespace="production", patch=None)
    paths = _generate_website(config, tmp_path / "output", _templates_override=templates_dir)

    # Check the Deployment got the namespace
    deployment_files = [p for p in paths if "deployment" in p.name]
    assert len(deployment_files) == 1
    deployment_doc = yaml.safe_load(deployment_files[0].read_text())
    assert deployment_doc["metadata"]["namespace"] == "production"

    # Check the ClusterRole did NOT get the namespace
    clusterrole_files = [p for p in paths if "clusterrole" in p.name]
    assert len(clusterrole_files) == 1
    clusterrole_doc = yaml.safe_load(clusterrole_files[0].read_text())
    assert "namespace" not in clusterrole_doc["metadata"]


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
