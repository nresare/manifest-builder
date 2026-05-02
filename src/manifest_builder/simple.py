# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Simple manifest generation from bundled Mustache templates."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from manifest_builder.config import (
    DEFAULT_REPLICA_COUNT,
    ManifestConfig,
    SimpleConfig,
    TemplateValue,
    validate_simple_config,
)
from manifest_builder.generator import (
    CLUSTER_SCOPED_KINDS,
    _make_k8s_name,
    _parse_variables,
    _write_documents,
)
from manifest_builder.handlers import ConfigHandler, GenerationContext
from manifest_builder.website import (
    _config_checksum,
    _configmap_suffix_from_mount_path,
    _make_configmaps,
)


class SimpleConfigHandler(ConfigHandler):
    """Generate manifests for simple configs."""

    def __init__(self, configs: Sequence[SimpleConfig] | None = None) -> None:
        self.configs = list(configs or [])

    def top_level_config_name(self) -> str:
        return "simple"

    def load_config(
        self,
        data: object,
        source_file: Path,
        root_config: dict[str, Any],
    ) -> None:
        if not isinstance(data, list):
            raise ValueError(f"'simple' must be a list of tables in {source_file}")

        variables = _parse_variables(root_config.get("variables"), source_file)
        for item in data:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Each [[simple]] entry must be a table in {source_file}"
                )
            self.configs.append(_parse_simple_config(item, source_file, variables))

    def iter_configs(self) -> list[SimpleConfig]:
        return self.configs

    def validate(self, config: ManifestConfig, repo_root: Path) -> None:
        if not isinstance(config, SimpleConfig):
            raise TypeError(
                f"SimpleConfigHandler cannot process {type(config).__name__}"
            )
        validate_simple_config(config)

    def generate(
        self,
        config: ManifestConfig,
        context: GenerationContext,
    ) -> set[Path]:
        if not isinstance(config, SimpleConfig):
            raise TypeError(
                f"SimpleConfigHandler cannot process {type(config).__name__}"
            )
        return generate_simple(
            config,
            context.output_dir,
            images=context.images,
        )


def _parse_config_files(data: object, source_file: Path) -> dict[str, Path] | None:
    """Parse config file mappings from inline or array-of-table TOML syntax."""
    if data is None:
        return None

    config_data: dict[str, str] = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Each [[simple.config]] entry must be a table in {source_file}"
                )
            config_data.update(_parse_config_file_table(item, source_file))
    elif isinstance(data, dict):
        config_data = _parse_config_file_table(data, source_file)
    else:
        raise ValueError(f"'simple.config' must be a table in {source_file}")

    config_dir = source_file.parent
    return {
        container_path: config_dir / local_path
        for container_path, local_path in config_data.items()
    }


def _parse_config_file_table(data: dict, source_file: Path) -> dict[str, str]:
    config_data: dict[str, str] = {}
    for container_path, local_path in data.items():
        if not isinstance(container_path, str) or not isinstance(local_path, str):
            raise ValueError(
                f"'simple.config' entries must map strings to strings in {source_file}"
            )
        config_data[container_path] = local_path
    return config_data


def _parse_simple_config(
    data: dict,
    source_file: Path,
    variables: dict[str, TemplateValue],
) -> SimpleConfig:
    """Parse a simple app configuration from TOML data."""
    for required_field in ("namespace", "image"):
        if required_field not in data:
            raise ValueError(
                f"Missing required field '{required_field}' in {source_file}"
            )

    iam_role = data.get("iam-role")
    if iam_role is not None and not isinstance(iam_role, str):
        raise ValueError(f"'iam-role' must be a string in {source_file}")

    k8s_role = data.get("k8s-role")
    if k8s_role is not None and not isinstance(k8s_role, str):
        raise ValueError(f"'k8s-role' must be a string in {source_file}")

    name = data.get("name", data["namespace"])
    extra_resources = None
    if "extra-resources" in data:
        extra_resources = source_file.parent / data["extra-resources"]

    return SimpleConfig(
        name=name,
        namespace=data["namespace"],
        image=data["image"],
        args=data.get("args"),
        iam_role=iam_role,
        k8s_role=k8s_role,
        config=_parse_config_files(data.get("config"), source_file),
        variables=variables.copy(),
        extra_resources=extra_resources,
        replicas=data.get("replicas", DEFAULT_REPLICA_COUNT),
    )


def _inject_configmaps(
    docs: list[dict],
    config: SimpleConfig,
    k8s_name: str,
) -> None:
    if not config.config:
        return

    configmaps = _make_configmaps(k8s_name, config.config)
    checksum = _config_checksum(configmaps)
    for cm in configmaps:
        cm.setdefault("metadata", {})["namespace"] = config.namespace
    docs.extend(configmaps)

    mount_groups = {
        str(Path(container_path).parent) for container_path in config.config
    }
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue

        doc.setdefault("spec", {}).setdefault("template", {}).setdefault(
            "metadata", {}
        ).setdefault("annotations", {})["checksum/config"] = checksum
        pod_spec = (
            doc.setdefault("spec", {}).setdefault("template", {}).setdefault("spec", {})
        )
        for mount_path in sorted(mount_groups):
            cm_name = f"{k8s_name}-{_configmap_suffix_from_mount_path(mount_path)}"
            for container in pod_spec.get("containers", []):
                container.setdefault("volumeMounts", []).append(
                    {"name": cm_name, "mountPath": mount_path}
                )
            pod_spec.setdefault("volumes", []).append(
                {"name": cm_name, "configMap": {"name": cm_name}}
            )


def generate_simple(
    config: SimpleConfig,
    output_dir: Path,
    images: dict[str, str] | None = None,
    _templates_override: Path | None = None,  # for testing only
) -> set[Path]:
    """Generate manifests for a simple app from bundled Mustache templates."""
    import pystache
    from pystache.common import MissingTags

    if _templates_override is not None:
        templates_dir = _templates_override
    else:
        from importlib.resources import files as get_package_files

        templates_dir = Path(
            str(get_package_files("manifest_builder") / "templates" / "simple")
        )

    context: dict[str, Any] = {
        **(images or {}),
        **config.variables,
        "name": config.name,
        "k8s_name": _make_k8s_name(config.name),
        "namespace": config.namespace,
        "replicas": config.replicas,
    }
    context["image"] = config.image
    if config.args:
        context["args"] = config.args
    if config.iam_role or config.k8s_role:
        context["service_account"] = True
    if config.iam_role:
        context["iam_role"] = pystache.Renderer(missing_tags=MissingTags.strict).render(
            config.iam_role, context
        )
    if config.k8s_role:
        context["k8s_role"] = pystache.Renderer(missing_tags=MissingTags.strict).render(
            config.k8s_role, context
        )

    docs: list[dict] = []
    for template_file in sorted(templates_dir.glob("*.yaml")):
        if template_file.name.startswith("_"):
            continue

        rendered = pystache.render(template_file.read_text(), context)
        for doc in yaml.safe_load_all(rendered):
            if doc:
                docs.append(doc)

    for doc in docs:
        kind = doc.get("kind")
        if kind and kind not in CLUSTER_SCOPED_KINDS:
            doc.setdefault("metadata", {})["namespace"] = config.namespace

    if config.extra_resources:
        renderer = pystache.Renderer(missing_tags=MissingTags.strict)
        for yaml_file in sorted(config.extra_resources.glob("*.yaml")):
            rendered = renderer.render(yaml_file.read_text(), context)
            for doc in yaml.safe_load_all(rendered):
                if not doc:
                    continue
                kind = doc.get("kind")
                if kind and kind not in CLUSTER_SCOPED_KINDS:
                    if "namespace" not in doc.get("metadata", {}):
                        doc.setdefault("metadata", {})["namespace"] = config.namespace
                docs.append(doc)

    k8s_name = _make_k8s_name(config.name)
    _inject_configmaps(docs, config, k8s_name)

    return _write_documents(docs, output_dir, config.namespace, config.name)
