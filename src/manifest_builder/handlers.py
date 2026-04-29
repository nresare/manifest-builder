# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Config handler interfaces for manifest generation."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from manifest_builder.config import ManifestConfig, ManifestConfigs
from manifest_builder.helm import ChartCacheStats


@dataclass(frozen=True)
class GenerationContext:
    """Shared inputs needed by concrete config handlers during generation."""

    output_dir: Path
    repo_root: Path
    charts_dir: Path
    verbose: bool = False
    images: dict[str, str] | None = None
    cache_stats: ChartCacheStats | None = None


class ConfigHandler(ABC):
    """Base class for config-type-specific manifest generation."""

    @abstractmethod
    def iter_configs(self, configs: ManifestConfigs) -> Iterable[ManifestConfig]:
        """Yield the configs this handler is responsible for."""

    @abstractmethod
    def validate(self, config: ManifestConfig, repo_root: Path) -> None:
        """Validate a config before any manifests are generated."""

    @abstractmethod
    def generate(
        self,
        config: ManifestConfig,
        context: GenerationContext,
    ) -> set[Path]:
        """Generate manifests for a config and return paths that were written."""
