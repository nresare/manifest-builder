# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for helm command execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from manifest_builder.helm import pull_chart


def test_pull_chart_uses_cached_chart(tmp_path: Path) -> None:
    """pull_chart should skip download if chart already exists."""
    chart_dir = tmp_path / "myapp"
    chart_dir.mkdir()

    result = pull_chart("myapp", "https://charts.example.com", tmp_path)

    assert result == chart_dir


def test_pull_chart_oci_uses_cached_chart(tmp_path: Path) -> None:
    """pull_chart should skip download if OCI chart already exists using extracted chart name."""
    # For OCI repos, the chart dir is named after the last component of the OCI URL
    chart_dir = tmp_path / "gateway-helm"
    chart_dir.mkdir()

    result = pull_chart(
        "envoyproxy", "oci://docker.io/envoyproxy/gateway-helm", tmp_path
    )

    assert result == chart_dir


@patch("manifest_builder.helm.subprocess.run")
def test_pull_chart_http_repository(mock_run: MagicMock, tmp_path: Path) -> None:
    """pull_chart should use --repo for HTTP/HTTPS repositories."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    pull_chart("cert-manager", "https://charts.jetstack.io", tmp_path, "v1.19.4")

    # Verify the command structure
    call_args = mock_run.call_args
    cmd = call_args[0][0]

    assert "helm" in cmd
    assert "pull" in cmd
    assert "cert-manager" in cmd
    assert "--repo" in cmd
    assert "https://charts.jetstack.io" in cmd
    assert "--version" in cmd
    assert "v1.19.4" in cmd


@patch("manifest_builder.helm.subprocess.run")
def test_pull_chart_oci_repository(mock_run: MagicMock, tmp_path: Path) -> None:
    """pull_chart should use OCI URL directly without --repo for OCI registries."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    pull_chart(
        "envoyproxy", "oci://docker.io/envoyproxy/gateway-helm", tmp_path, "v1.3.3"
    )

    # Verify the command structure
    call_args = mock_run.call_args
    cmd = call_args[0][0]

    assert "helm" in cmd
    assert "pull" in cmd
    # For OCI, the repo URL is used directly (chart name not appended)
    assert "oci://docker.io/envoyproxy/gateway-helm" in cmd
    # --repo should NOT be used for OCI
    assert "--repo" not in cmd
    assert "--version" in cmd
    assert "v1.3.3" in cmd


@patch("manifest_builder.helm.subprocess.run")
def test_pull_chart_oci_without_version(mock_run: MagicMock, tmp_path: Path) -> None:
    """pull_chart should work for OCI registries without a version."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    pull_chart("envoyproxy", "oci://docker.io/envoyproxy/gateway-helm", tmp_path)

    # Verify the command structure
    call_args = mock_run.call_args
    cmd = call_args[0][0]

    assert "helm" in cmd
    assert "pull" in cmd
    # For OCI, the repo URL is used directly
    assert "oci://docker.io/envoyproxy/gateway-helm" in cmd
    assert "--repo" not in cmd
    assert "--version" not in cmd
