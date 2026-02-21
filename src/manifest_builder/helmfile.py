# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Helmfile YAML parsing for chart source resolution."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class HelmfileRepository:
    """A Helm chart repository."""
    name: str
    url: str


@dataclass
class HelmfileRelease:
    """A release entry from helmfile.yaml."""
    name: str
    chart: str  # "reponame/chartname" format
    version: str | None
    namespace: str | None


@dataclass
class Helmfile:
    """Parsed helmfile.yaml content."""
    repositories: list[HelmfileRepository]
    releases: list[HelmfileRelease]


def load_helmfile(path: Path) -> Helmfile:
    """
    Parse a helmfile.yaml file.

    Args:
        path: Path to helmfile.yaml

    Returns:
        Parsed helmfile with repositories and releases

    Raises:
        FileNotFoundError: If the file does not exist
        ValueError: If the file is malformed
    """
    if not path.exists():
        raise FileNotFoundError(f"helmfile.yaml not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"helmfile.yaml must be a YAML mapping: {path}")

    repositories: list[HelmfileRepository] = []
    for repo in data.get("repositories") or []:
        if "name" not in repo or "url" not in repo:
            raise ValueError(f"Each repository entry requires 'name' and 'url': {path}")
        repositories.append(HelmfileRepository(name=repo["name"], url=repo["url"]))

    releases: list[HelmfileRelease] = []
    for rel in data.get("releases") or []:
        if "name" not in rel or "chart" not in rel:
            raise ValueError(f"Each release entry requires 'name' and 'chart': {path}")
        releases.append(
            HelmfileRelease(
                name=rel["name"],
                chart=rel["chart"],
                version=rel.get("version"),
                namespace=rel.get("namespace"),
            )
        )

    return Helmfile(repositories=repositories, releases=releases)
