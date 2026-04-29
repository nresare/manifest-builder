# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Copy manifest generation from existing manifests."""

from pathlib import Path

import pystache
import yaml
from pystache.common import MissingTags

from manifest_builder.config import (
    CopyConfig,
    ManifestConfig,
    ManifestConfigs,
    validate_copy_config,
)
from manifest_builder.generator import (
    CLUSTER_SCOPED_KINDS,
    _make_k8s_name,
    _write_documents,
)
from manifest_builder.handlers import ConfigHandler, GenerationContext
from manifest_builder.website import (
    _config_checksum,
    _configmap_suffix_from_mount_path,
    _make_configmaps,
)


class CopyConfigHandler(ConfigHandler):
    """Generate manifests for copy configs."""

    def iter_configs(self, configs: ManifestConfigs) -> list[CopyConfig]:
        return configs.copies

    def validate(self, config: ManifestConfig, repo_root: Path) -> None:
        if not isinstance(config, CopyConfig):
            raise TypeError(f"CopyConfigHandler cannot process {type(config).__name__}")
        validate_copy_config(config)

    def generate(
        self,
        config: ManifestConfig,
        context: GenerationContext,
    ) -> set[Path]:
        if not isinstance(config, CopyConfig):
            raise TypeError(f"CopyConfigHandler cannot process {type(config).__name__}")
        return generate_copy(config, context.output_dir, images=context.images)


def generate_copy(
    config: CopyConfig,
    output_dir: Path,
    images: dict[str, str] | None = None,
) -> set[Path]:
    """Generate manifests for a copy app by copying existing manifests.

    Reads all YAML files from the source directory, injects the configured
    namespace into any namespaced resources that don't already have one, and
    optionally creates a ConfigMap from the files listed in the config table.

    If images are provided, the manifests are processed as Mustache templates,
    replacing {{variable}} with the corresponding image reference.

    Args:
        config: Copy app configuration
        output_dir: Directory to write generated manifests
        images: Dict mapping image variable names to image references

    Returns:
        Set of paths written
    """
    renderer = pystache.Renderer(missing_tags=MissingTags.strict)
    docs: list[dict] = []
    context = images or {}

    for yaml_file in sorted(config.source.glob("*.yaml")):
        text = yaml_file.read_text()
        text = renderer.render(text, context)

        for doc in yaml.safe_load_all(text):
            if doc:
                kind = doc.get("kind")
                if kind and kind not in CLUSTER_SCOPED_KINDS:
                    if "namespace" not in doc.get("metadata", {}):
                        doc.setdefault("metadata", {})["namespace"] = config.namespace
                docs.append(doc)

    if config.config:
        k8s_name = _make_k8s_name(config.name)
        configmaps = _make_configmaps(k8s_name, config.config)
        checksum = _config_checksum(configmaps)
        for cm in configmaps:
            cm.setdefault("metadata", {})["namespace"] = config.namespace
        docs.extend(configmaps)

        mount_groups = {
            str(Path(container_path).parent) for container_path in config.config
        }
        for doc in docs:
            if doc.get("kind") == "Deployment":
                doc.setdefault("spec", {}).setdefault("template", {}).setdefault(
                    "metadata", {}
                ).setdefault("annotations", {})["checksum/config"] = checksum
                pod_spec = (
                    doc.setdefault("spec", {})
                    .setdefault("template", {})
                    .setdefault("spec", {})
                )
                for mount_path in sorted(mount_groups):
                    cm_name = (
                        f"{k8s_name}-{_configmap_suffix_from_mount_path(mount_path)}"
                    )
                    for container in pod_spec.get("containers", []):
                        container.setdefault("volumeMounts", []).append(
                            {"name": cm_name, "mountPath": mount_path}
                        )
                    pod_spec.setdefault("volumes", []).append(
                        {"name": cm_name, "configMap": {"name": cm_name}}
                    )

    return _write_documents(docs, output_dir, config.namespace, config.name)
