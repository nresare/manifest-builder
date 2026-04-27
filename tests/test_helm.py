# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for helm command execution."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from manifest_builder.helm import ChartCacheStats, pull_chart


def test_pull_chart_uses_cached_chart(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """pull_chart should skip download if chart already exists."""
    chart_dir = tmp_path / "myapp"
    chart_dir.mkdir()
    cache_stats = ChartCacheStats()

    caplog.set_level(logging.DEBUG, logger="manifest_builder.helm")
    result = pull_chart(
        "myapp", tmp_path, repo="https://charts.example.com", cache_stats=cache_stats
    )

    assert result == chart_dir
    assert "Chart cache hit: myapp" in caplog.text
    assert cache_stats.hits == 1
    assert cache_stats.misses == 0
    cache_records = [
        record for record in caplog.records if record.message.startswith("Chart cache")
    ]
    assert [record.levelno for record in cache_records] == [logging.DEBUG]


def test_pull_chart_oci_uses_cached_chart(tmp_path: Path) -> None:
    """pull_chart should skip download if OCI chart already exists using extracted chart name."""
    # For OCI repos, the chart dir is named after the last component of the OCI URL
    chart_dir = tmp_path / "gateway-helm"
    chart_dir.mkdir()

    result = pull_chart("oci://docker.io/envoyproxy/gateway-helm", tmp_path)

    assert result == chart_dir


@patch("manifest_builder.helm.subprocess.run")
def test_pull_chart_http_repository(
    mock_run: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """pull_chart should use --repo for HTTP/HTTPS repositories."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    cache_stats = ChartCacheStats()

    caplog.set_level(logging.DEBUG, logger="manifest_builder.helm")
    pull_chart(
        "cert-manager",
        tmp_path,
        repo="https://charts.jetstack.io",
        version="v1.19.4",
        cache_stats=cache_stats,
    )

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
    assert "Chart cache miss: cert-manager" in caplog.text
    assert cache_stats.hits == 0
    assert cache_stats.misses == 1
    miss_records = [
        record
        for record in caplog.records
        if record.message.startswith("Chart cache miss:")
    ]
    assert [record.levelno for record in miss_records] == [logging.DEBUG]


@patch("manifest_builder.helm.subprocess.run")
def test_pull_chart_oci_repository(mock_run: MagicMock, tmp_path: Path) -> None:
    """pull_chart should use OCI URL directly without --repo for OCI registries."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    pull_chart("oci://docker.io/envoyproxy/gateway-helm", tmp_path, version="v1.3.3")

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

    pull_chart("oci://docker.io/envoyproxy/gateway-helm", tmp_path)

    # Verify the command structure
    call_args = mock_run.call_args
    cmd = call_args[0][0]

    assert "helm" in cmd
    assert "pull" in cmd
    # For OCI, the repo URL is used directly
    assert "oci://docker.io/envoyproxy/gateway-helm" in cmd
    assert "--repo" not in cmd
    assert "--version" not in cmd
