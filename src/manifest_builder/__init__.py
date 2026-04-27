# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Manifest Builder - Generate Kubernetes manifests from Helm charts."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = str(
        getattr(import_module("manifest_builder._version"), "__version__")
    )
except ModuleNotFoundError:
    try:
        __version__ = version("manifest-builder")
    except PackageNotFoundError:
        __version__ = "0.0.0"
