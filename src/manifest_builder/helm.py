"""Helm command execution for generating manifests."""

import subprocess
from pathlib import Path


def check_helm_available() -> bool:
    """
    Check if helm is installed and available.

    Returns:
        True if helm is available, False otherwise
    """
    try:
        result = subprocess.run(
            ["helm", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False


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
        version: Optional chart version

    Returns:
        Generated YAML manifests as a string

    Raises:
        subprocess.CalledProcessError: If helm command fails
        RuntimeError: If helm is not available
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

    # Add values files
    for values_file in values_files:
        cmd.extend(["-f", str(values_file)])

    # Add version if specified
    if version:
        cmd.extend(["--version", version])

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
        error_msg = f"helm template failed for {release_name}: {e.stderr}"
        raise RuntimeError(error_msg) from e
    except subprocess.TimeoutExpired as e:
        error_msg = f"helm template timed out for {release_name}"
        raise RuntimeError(error_msg) from e
