# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Website manifest generation from Mustache templates."""

from pathlib import Path

import yaml

from manifest_builder.config import WebsiteConfig
from manifest_builder.generator import CLUSTER_SCOPED_KINDS, _make_k8s_name, _write_documents


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


def generate_website(
    config: WebsiteConfig,
    output_dir: Path,
    verbose: bool = False,
    _templates_override: Path | None = None,  # for testing only
) -> set[Path]:
    """Generate manifests for a website app from bundled Mustache templates.

    Args:
        config: Website configuration
        output_dir: Directory to write generated manifests
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

        templates_dir = Path(str(get_package_files("manifest_builder") / "templates" / "web"))

    # Prepare the template context with name, k8s_name, and optional image/args/git_repo
    context = {
        "name": config.name,
        "k8s_name": _make_k8s_name(config.name),
    }
    if config.image:
        context["image"] = config.image
    if config.args:
        context["args"] = config.args
    if config.hugo_repo:
        context["git_repo"] = config.hugo_repo

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
            doc.setdefault("metadata", {})[
                "namespace"
            ] = config.namespace

    # Apply hugo_repo annotation to Deployment objects if configured
    if config.hugo_repo:
        for doc in docs:
            if doc.get("kind") == "Deployment":
                doc.setdefault("metadata", {}).setdefault("annotations", {})[
                    "hugo"
                ] = config.hugo_repo

    return _write_documents(docs, output_dir, config.namespace, verbose, config.name)
