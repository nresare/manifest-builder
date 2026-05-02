# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for the command-line interface."""

from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from manifest_builder.cli import main


@mock.patch("manifest_builder.cli.generate")
@mock.patch("manifest_builder.cli.get_helm_version", return_value="v3.0.0")
def test_main_delegates_to_generate(
    mock_get_helm_version: mock.Mock,
    mock_generate: mock.Mock,
) -> None:
    """The Click command sets up CLI concerns and delegates generation."""
    result = CliRunner().invoke(
        main,
        ["--config-dir", "conf", "--output-dir", "out", "--create-commit"],
    )

    assert result.exit_code == 0
    mock_get_helm_version.assert_called_once_with()
    mock_generate.assert_called_once_with(
        Path("conf"),
        Path("out"),
        verbose=False,
        create_commit=True,
        allow_dirty_config=False,
    )


@mock.patch("manifest_builder.cli.generate", side_effect=ValueError("bad config"))
@mock.patch("manifest_builder.cli.get_helm_version", return_value="v3.0.0")
def test_main_reports_generate_errors(
    mock_get_helm_version: mock.Mock,
    mock_generate: mock.Mock,
) -> None:
    """Generation errors are rendered through the existing Click error path."""
    result = CliRunner().invoke(main)

    assert result.exit_code == 1
    assert "Configuration error: bad config" in result.output
    mock_get_helm_version.assert_called_once_with()
    mock_generate.assert_called_once()
