# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Return types for manifest generation."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, order=True)
class KubernetesObjectRef:
    """Stable identity for a Kubernetes object."""

    kind: str
    namespace: str | None
    name: str


@dataclass
class GenerationResult:
    """Summary of manifests written and object-level git changes."""

    written_paths: set[Path] = field(default_factory=set)
    created_or_modified: set[KubernetesObjectRef] = field(default_factory=set)
    removed: set[KubernetesObjectRef] = field(default_factory=set)
    deploy_id: str | None = None
