# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Simple manifest generation by copying existing manifests."""

from pathlib import Path

import pystache
import yaml
from pystache.common import MissingTags

from manifest_builder.config import SimpleConfig
from manifest_builder.generator import (
    CLUSTER_SCOPED_KINDS,
    _make_k8s_name,
    _write_documents,
)
from manifest_builder.website import _make_configmaps


def generate_simple(
    config: SimpleConfig,
    output_dir: Path,
    images: dict[str, str] | None = None,
) -> set[Path]:
    """Generate manifests for a simple app by copying existing manifests.

    Reads all YAML files from the copy-from directory, injects the configured
    namespace into any namespaced resources that don't already have one, and
    optionally creates a ConfigMap from the files listed in the config table.

    If images are provided, the manifests are processed as Mustache templates,
    replacing {{variable}} with the corresponding image reference.

    Args:
        config: Simple app configuration
        output_dir: Directory to write generated manifests
        images: Dict mapping image variable names to image references

    Returns:
        Set of paths written
    """
    renderer = pystache.Renderer(missing_tags=MissingTags.strict)
    docs: list[dict] = []
    context = images or {}

    for yaml_file in sorted(config.copy_from.glob("*.yaml")):
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
        for cm in configmaps:
            cm.setdefault("metadata", {})["namespace"] = config.namespace
        docs.extend(configmaps)

    return _write_documents(docs, output_dir, config.namespace, config.name)
