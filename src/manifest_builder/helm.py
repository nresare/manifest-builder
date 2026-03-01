# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Helm command execution for generating manifests."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_helm_version() -> str:
    """
    Get the installed helm version.

    Returns:
        Helm version string (e.g., "v3.12.0")

    Raises:
        RuntimeError: If helm is not available or version check fails
    """
    try:
        result = subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(f"helm version check failed: {result.stderr}")
        return result.stdout.strip()
    except FileNotFoundError as e:
        raise RuntimeError(
            "helm is not installed or not available in PATH. "
            "Please install helm: https://helm.sh/docs/intro/install/"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("helm version check timed out") from e


def check_helm_available() -> bool:
    """
    Check if helm is installed and available.

    Returns:
        True if helm is available, False otherwise
    """
    try:
        subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def pull_chart(
    chart: str,
    repo: str,
    dest: Path,
    version: str | None = None,
) -> Path:
    """
    Pull a chart from a repository and untar it to a local directory.

    Skips the pull if the destination already exists.

    Supports both traditional HTTP/HTTPS repositories and OCI registries.
    For OCI registries (repo URLs starting with oci://), the chart reference
    is the OCI URL itself, and the actual chart name is extracted from the URL.

    Args:
        chart: Chart name within the repository (repo reference for OCI, chart name for HTTP/HTTPS)
        repo: Repository URL (HTTP/HTTPS or OCI format)
        dest: Directory to untar the chart into
        version: Optional chart version

    Returns:
        Path to the untarred chart directory

    Raises:
        RuntimeError: If helm pull fails
    """
    # Determine if this is an OCI registry
    is_oci = repo.startswith("oci://")

    # For OCI repos, extract the actual chart name from the URL (last component)
    # For HTTP/HTTPS repos, use the provided chart name
    if is_oci:
        actual_chart_name = repo.rstrip("/").split("/")[-1]
    else:
        actual_chart_name = chart

    chart_dir = dest / actual_chart_name

    if chart_dir.exists():
        logger.debug(f"Using cached chart at {chart_dir}")
        return chart_dir

    dest.mkdir(parents=True, exist_ok=True)

    version_str = f" (version {version})" if version else ""
    logger.info(f"Downloading chart {chart} from {repo}{version_str}")

    cmd = ["helm", "pull"]

    if is_oci:
        # For OCI registries, the repo URL is the full chart reference
        cmd.append(repo)
    else:
        # For HTTP/HTTPS repositories, use --repo flag with chart name
        cmd.append(chart)
        cmd.extend(["--repo", repo])

    cmd.extend(["--untar", "--untardir", str(dest)])

    if version:
        cmd.extend(["--version", version])

    logger.debug(f"Executing: {' '.join(cmd)}")

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        logger.debug(f"Successfully unpacked chart to {chart_dir}")
    except subprocess.CalledProcessError as e:
        cmd_str = " ".join(cmd)
        raise RuntimeError(
            f"helm pull failed for {chart}:\n  Command: {cmd_str}\n  Error: {e.stderr}"
        ) from e
    except subprocess.TimeoutExpired as e:
        cmd_str = " ".join(cmd)
        raise RuntimeError(
            f"helm pull timed out for {chart}:\n  Command: {cmd_str}"
        ) from e

    return chart_dir


def run_helm_template(
    release_name: str,
    chart: str,
    namespace: str,
    values_files: list[Path],
    version: str | None = None,
) -> str:
    """
    Execute helm template command and return the generated manifests.

    Args:
        release_name: Name of the Helm release
        chart: Chart reference (repo/chart, local path, or OCI URL)
        namespace: Kubernetes namespace
        values_files: List of values files to apply
        version: Optional chart version (ignored for local paths)

    Returns:
        Generated YAML manifests as a string

    Raises:
        RuntimeError: If helm is not available or the command fails
    """
    if not check_helm_available():
        raise RuntimeError(
            "helm is not installed or not available in PATH. "
            "Please install helm: https://helm.sh/docs/intro/install/"
        )

    cmd = [
        "helm",
        "template",
        release_name,
        chart,
        "--namespace",
        namespace,
    ]

    for values_file in values_files:
        cmd.extend(["-f", str(values_file)])

    if version:
        cmd.extend(["--version", version])

    logger.debug(f"Executing: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        cmd_str = " ".join(cmd)
        raise RuntimeError(
            f"helm template failed for {release_name}:\n  Command: {cmd_str}\n  Error: {e.stderr}"
        ) from e
    except subprocess.TimeoutExpired as e:
        cmd_str = " ".join(cmd)
        raise RuntimeError(
            f"helm template timed out for {release_name}:\n  Command: {cmd_str}"
        ) from e
