# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for helmfile.yaml parsing."""

import textwrap
from pathlib import Path

import pytest

from manifest_builder.helmfile import load_helmfile


def write_helmfile(directory: Path, content: str) -> Path:
    path = directory / "helmfile.yaml"
    path.write_text(textwrap.dedent(content))
    return path


def test_load_helmfile_repositories_and_releases(tmp_path: Path) -> None:
    write_helmfile(
        tmp_path,
        """\
        repositories:
          - name: jetstack
            url: https://charts.jetstack.io
        releases:
          - name: cert-manager
            chart: jetstack/cert-manager
            version: v1.18.2
            namespace: cert-manager
        """,
    )

    hf = load_helmfile(tmp_path / "helmfile.yaml")

    assert len(hf.repositories) == 1
    assert hf.repositories[0].name == "jetstack"
    assert hf.repositories[0].url == "https://charts.jetstack.io"

    assert len(hf.releases) == 1
    rel = hf.releases[0]
    assert rel.name == "cert-manager"
    assert rel.chart == "jetstack/cert-manager"
    assert rel.version == "v1.18.2"
    assert rel.namespace == "cert-manager"


def test_load_helmfile_version_optional(tmp_path: Path) -> None:
    write_helmfile(
        tmp_path,
        """\
        releases:
          - name: myapp
            chart: myrepo/myapp
        """,
    )

    hf = load_helmfile(tmp_path / "helmfile.yaml")
    assert hf.releases[0].version is None


def test_load_helmfile_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_helmfile(tmp_path / "helmfile.yaml")


def test_load_helmfile_missing_repo_name(tmp_path: Path) -> None:
    write_helmfile(
        tmp_path,
        """\
        repositories:
          - url: https://charts.example.com
        """,
    )
    with pytest.raises(ValueError, match="requires 'name' and 'url'"):
        load_helmfile(tmp_path / "helmfile.yaml")


def test_load_helmfile_missing_release_chart(tmp_path: Path) -> None:
    write_helmfile(
        tmp_path,
        """\
        releases:
          - name: myapp
        """,
    )
    with pytest.raises(ValueError, match="requires 'name' and 'chart'"):
        load_helmfile(tmp_path / "helmfile.yaml")


def test_load_helmfile_empty_sections(tmp_path: Path) -> None:
    write_helmfile(tmp_path, "{}\n")
    hf = load_helmfile(tmp_path / "helmfile.yaml")
    assert hf.repositories == []
    assert hf.releases == []
