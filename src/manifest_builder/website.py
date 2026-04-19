# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Website manifest generation from Mustache templates."""

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from manifest_builder.config import WebsiteConfig
from manifest_builder.generator import (
    CLUSTER_SCOPED_KINDS,
    _make_k8s_name,
    _write_documents,
)


def _load_fragments(templates_dir: Path, context: dict) -> dict[str, dict]:
    """Load and render underscore-prefixed fragment templates.

    Fragment templates (files starting with _) are rendered with the same Mustache
    context as regular templates but are not written to output. They can be used by
    website.py code to programmatically inject content into documents.

    Args:
        templates_dir: Directory containing template files
        context: Mustache rendering context

    Returns:
        Dict mapping fragment name (filename without leading _ and .yaml suffix)
        to the parsed YAML document
    """
    import pystache

    fragments = {}
    for fragment_file in sorted(templates_dir.glob("_*.yaml")):
        # Strip leading underscore and .yaml suffix to get the fragment name
        name = fragment_file.stem[1:]
        template_source = fragment_file.read_text()
        rendered = pystache.render(template_source, context)
        doc = yaml.safe_load(rendered)
        if doc:
            fragments[name] = doc
    return fragments


def _secret_name_from_mount_path(mount_path: str) -> str:
    """Generate a secret name from a mount path.

    Removes the leading / and converts subsequent / to -.

    Examples:
        "/email-password" -> "email-password"
        "/config/database" -> "config-database"

    Args:
        mount_path: The mount path (e.g., "/email-password")

    Returns:
        The generated secret name
    """
    if not mount_path.startswith("/"):
        raise ValueError(f"Mount path must start with /: {mount_path}")
    return mount_path[1:].replace("/", "-")


def _make_configmaps(k8s_name: str, config_files: dict[str, Path]) -> list[dict]:
    """Build ConfigMap objects grouped by the first component of each container path.

    Args:
        k8s_name: Kubernetes-safe name for the website (used in ConfigMap names)
        config_files: Dict mapping container path -> resolved local file path

    Returns:
        List of ConfigMap dictionaries grouped by top-level directory
    """
    groups: dict[str, dict[str, str]] = {}
    for container_path, local_path in config_files.items():
        parts = Path(container_path).parts
        if len(parts) < 2:
            raise ValueError(f"Config file path must be absolute: {container_path}")
        top_level = parts[1]
        data_key = str(Path(*parts[2:])) if len(parts) > 2 else "."
        groups.setdefault(top_level, {})[data_key] = local_path.read_text()

    return [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"{k8s_name}-{top_level}"},
            "data": data,
        }
        for top_level, data in sorted(groups.items())
    ]


def _config_checksum(configmaps: list[dict]) -> str:
    """Build a deterministic checksum for generated ConfigMap contents."""
    normalized = [
        {
            "name": configmap["metadata"]["name"],
            "data": {
                key: value for key, value in sorted(configmap.get("data", {}).items())
            },
        }
        for configmap in sorted(configmaps, key=lambda item: item["metadata"]["name"])
    ]
    payload = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _inject_custom_token_projection(doc: dict, audience: str) -> None:
    """Inject a projected service account token volume into a Deployment."""
    if doc.get("kind") != "Deployment":
        return

    pod_spec = (
        doc.setdefault("spec", {}).setdefault("template", {}).setdefault("spec", {})
    )

    for container in pod_spec.get("containers", []):
        container.setdefault("volumeMounts", []).append(
            {
                "name": "tokens",
                "mountPath": "/var/run/secrets/tokens",
                "readOnly": True,
            }
        )

    pod_spec.setdefault("volumes", []).append(
        {
            "name": "tokens",
            "projected": {
                "sources": [
                    {
                        "serviceAccountToken": {
                            "path": audience,
                            "expirationSeconds": 3600,
                            "audience": audience,
                        }
                    }
                ]
            },
        }
    )


def generate_website(
    config: WebsiteConfig,
    output_dir: Path,
    images: dict[str, str] | None = None,
    verbose: bool = False,
    _templates_override: Path | None = None,  # for testing only
) -> set[Path]:
    """Generate manifests for a website app from bundled Mustache templates.

    Args:
        config: Website configuration
        output_dir: Directory to write generated manifests
        images: Dict mapping image variable names to image references (e.g., {"git_image": "alpine/git:2.47.2"})
        verbose: If True, print detailed output
        _templates_override: Override templates directory (for testing only)

    Returns:
        Set of paths written
    """
    import pystache

    # Use the bundled website templates from the package (or override for testing)
    if _templates_override is not None:
        templates_dir = _templates_override
    else:
        from importlib.resources import files as get_package_files

        templates_dir = Path(
            str(get_package_files("manifest_builder") / "templates" / "web")
        )

    # Prepare the template context with name, k8s_name, and optional image/args/git_repo
    context: dict[str, Any] = {
        "name": config.name,
        "k8s_name": _make_k8s_name(config.name),
        "replicas": config.replicas,
    }
    if images:
        context.update(images)
    if config.image:
        context["image"] = config.image
    if config.args:
        context["args"] = config.args
    if config.hugo_repo:
        context["git_repo"] = config.hugo_repo
    if config.extra_hostnames:
        normalized = (
            config.extra_hostnames
            if isinstance(config.extra_hostnames, list)
            else [config.extra_hostnames]
        )

        class ExtraHostname:
            def __init__(self, hostname: str, k8s_hostname: str) -> None:
                self.hostname = hostname
                self.k8s_hostname = k8s_hostname

        context["extra_hostnames"] = [
            ExtraHostname(h, _make_k8s_name(h)) for h in normalized
        ]
        context["has_extra_hostnames"] = True

    docs: list[dict] = []
    for template_file in sorted(templates_dir.glob("*.yaml")):
        # Skip fragment templates (starting with underscore)
        if template_file.name.startswith("_"):
            continue

        with open(template_file) as f:
            template_source = f.read()

        # Render the Mustache template
        rendered = pystache.render(template_source, context)

        # Parse the rendered YAML documents
        for doc in yaml.safe_load_all(rendered):
            if doc:
                docs.append(doc)

    # Load fragment templates for use in post-processing injection logic
    fragments = _load_fragments(templates_dir, context)

    # Add the namespace metadata to namespaced resources
    for doc in docs:
        kind = doc.get("kind")
        if kind and kind not in CLUSTER_SCOPED_KINDS:
            doc.setdefault("metadata", {})["namespace"] = config.namespace

    # Apply Hugo fragments and annotations if configured
    if config.hugo_repo:
        for doc in docs:
            if doc.get("kind") == "Deployment":
                # Inject Hugo init containers if available
                if "hugo_initcontainers" in fragments:
                    doc.setdefault("spec", {}).setdefault("template", {}).setdefault(
                        "spec", {}
                    )["initContainers"] = fragments["hugo_initcontainers"]

                # Inject Hugo container if available (replaces existing containers)
                if "hugo_container" in fragments:
                    doc.setdefault("spec", {}).setdefault("template", {}).setdefault(
                        "spec", {}
                    )["containers"] = [fragments["hugo_container"]]

                # Inject Hugo volumes if available
                if "hugo_volumes" in fragments:
                    doc.setdefault("spec", {}).setdefault("template", {}).setdefault(
                        "spec", {}
                    )["volumes"] = fragments["hugo_volumes"]

                # Add Hugo repo annotation
                doc.setdefault("metadata", {}).setdefault("annotations", {})["hugo"] = (
                    config.hugo_repo
                )

    # Generate ConfigMaps from config files and inject volumes/mounts if configured
    if config.config:
        k8s_name = _make_k8s_name(config.name)
        configmaps = _make_configmaps(k8s_name, config.config)
        checksum = _config_checksum(configmaps)
        # Inject namespace into ConfigMaps (they're added after the main namespace loop)
        for cm in configmaps:
            cm.setdefault("metadata", {})["namespace"] = config.namespace
        docs.extend(configmaps)

        # Determine mount points from config (grouped by top-level directory)
        mount_groups = {
            Path(container_path).parts[1] for container_path in config.config
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
                for top_level in sorted(mount_groups):
                    cm_name = f"{k8s_name}-{top_level}"
                    # Add volumeMount to each container
                    for container in pod_spec.get("containers", []):
                        container.setdefault("volumeMounts", []).append(
                            {"name": cm_name, "mountPath": f"/{top_level}"}
                        )
                    # Add volume at pod level
                    pod_spec.setdefault("volumes", []).append(
                        {"name": cm_name, "configMap": {"name": cm_name}}
                    )

    # Handle external secrets if configured
    if config.external_secrets:
        k8s_name = _make_k8s_name(config.name)
        for mount_path in config.external_secrets:
            secret_name = _secret_name_from_mount_path(mount_path)
            # Inject volumes and mounts into Deployment
            for doc in docs:
                if doc.get("kind") == "Deployment":
                    pod_spec = (
                        doc.setdefault("spec", {})
                        .setdefault("template", {})
                        .setdefault("spec", {})
                    )
                    # Add volumeMount to each container
                    for container in pod_spec.get("containers", []):
                        container.setdefault("volumeMounts", []).append(
                            {"name": secret_name, "mountPath": mount_path}
                        )
                    # Add volume at pod level
                    pod_spec.setdefault("volumes", []).append(
                        {"name": secret_name, "secret": {"secretName": secret_name}}
                    )

    if config.custom_token_audience:
        for doc in docs:
            _inject_custom_token_projection(doc, config.custom_token_audience)

    return _write_documents(docs, output_dir, config.namespace, config.name)
