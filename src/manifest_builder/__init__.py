# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Manifest Builder - Generate Kubernetes manifests from Helm charts."""

from importlib.metadata import PackageNotFoundError, version

try:
    from manifest_builder._version import __version__
except ModuleNotFoundError:
    try:
        __version__ = version("manifest-builder")
    except PackageNotFoundError:
        __version__ = "0.0.0"
