# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for the command-line interface."""

from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from manifest_builder.cli import main


def test_diff_and_create_commit_are_mutually_exclusive() -> None:
    """The diff mode is an alternative to commit creation, not an add-on."""
    result = CliRunner().invoke(main, ["--diff", "--create-commit"])

    assert result.exit_code == 1
    assert "Use only one of --create-commit or --diff." in result.output


@mock.patch("manifest_builder.cli.get_manifest_diff", return_value="")
@mock.patch("manifest_builder.cli.generate_manifests", return_value=set())
@mock.patch("manifest_builder.cli.load_images", return_value={})
@mock.patch("manifest_builder.cli.resolve_configs", return_value=[])
@mock.patch("manifest_builder.cli.load_configs", return_value=[])
@mock.patch("manifest_builder.cli.get_helm_version", return_value="v3.0.0")
@mock.patch("manifest_builder.cli.is_git_dirty", return_value=False)
def test_diff_reports_when_output_is_identical(
    mock_is_git_dirty: mock.Mock,
    mock_get_helm_version: mock.Mock,
    mock_load_configs: mock.Mock,
    mock_resolve_configs: mock.Mock,
    mock_load_images: mock.Mock,
    mock_generate_manifests: mock.Mock,
    mock_get_manifest_diff: mock.Mock,
) -> None:
    """Diff mode reports explicitly when there is no diff to display."""
    result = CliRunner().invoke(main, ["--diff"])

    assert result.exit_code == 0
    assert "The output is identical before and after this change\n" in result.output
    mock_is_git_dirty.assert_called_once()
    mock_get_manifest_diff.assert_called_once_with(Path.cwd() / "output", set())
