# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Simple manifest generation by copying existing manifests."""

from pathlib import Path

import yaml

from manifest_builder.config import SimpleConfig
from manifest_builder.generator import (
    CLUSTER_SCOPED_KINDS,
    _make_k8s_name,
    _write_documents,
)
from manifest_builder.website import _make_configmaps


def generate_simple(config: SimpleConfig, output_dir: Path) -> set[Path]:
    """Generate manifests for a simple app by copying existing manifests.

    Reads all YAML files from the copy-from directory, injects the configured
    namespace into any namespaced resources that don't already have one, and
    optionally creates a ConfigMap from the files listed in the config table.

    Args:
        config: Simple app configuration
        output_dir: Directory to write generated manifests

    Returns:
        Set of paths written
    """
    docs: list[dict] = []

    for yaml_file in sorted(config.copy_from.glob("*.yaml")):
        for doc in yaml.safe_load_all(yaml_file.read_text()):
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
