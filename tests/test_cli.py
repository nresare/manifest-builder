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
        vars_from=None,
        namespace=None,
    )


@mock.patch("manifest_builder.cli.generate")
@mock.patch("manifest_builder.cli.get_helm_version", return_value="v3.0.0")
def test_main_passes_vars_from(
    mock_get_helm_version: mock.Mock,
    mock_generate: mock.Mock,
) -> None:
    """The --vars-from option is forwarded to generate()."""
    result = CliRunner().invoke(main, ["--vars-from", "extra.toml"])

    assert result.exit_code == 0
    mock_get_helm_version.assert_called_once_with()
    mock_generate.assert_called_once_with(
        Path("conf"),
        Path("output"),
        verbose=False,
        create_commit=False,
        allow_dirty_config=False,
        vars_from=Path("extra.toml"),
        namespace=None,
    )


@mock.patch("manifest_builder.cli.generate")
@mock.patch("manifest_builder.cli.get_helm_version", return_value="v3.0.0")
def test_main_passes_namespace(
    mock_get_helm_version: mock.Mock,
    mock_generate: mock.Mock,
) -> None:
    """The --namespace option is forwarded to generate()."""
    result = CliRunner().invoke(main, ["--namespace", "team-a"])

    assert result.exit_code == 0
    mock_get_helm_version.assert_called_once_with()
    mock_generate.assert_called_once_with(
        Path("conf"),
        Path("output"),
        verbose=False,
        create_commit=False,
        allow_dirty_config=False,
        vars_from=None,
        namespace="team-a",
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


@mock.patch("manifest_builder.cli.get_helm_version", return_value="v3.0.0")
def test_main_reports_releases_yaml_scanner_error_concisely(
    mock_get_helm_version: mock.Mock,
    tmp_path: Path,
) -> None:
    """YAML syntax errors in releases.yaml are logged without a traceback."""
    config_dir = tmp_path / "conf"
    output_dir = tmp_path / "out"
    config_dir.mkdir()
    output_dir.mkdir()
    (config_dir / "releases.yaml").write_text(
        "repositories:\n  - name envoyproxy\n    url: docker.io/envoyproxy\n"
    )

    result = CliRunner().invoke(
        main,
        ["--config-dir", str(config_dir), "--output-dir", str(output_dir)],
    )

    assert result.exit_code == 1
    assert (
        '[CRITICAL] mapping values are not allowed here in "releases.yaml", '
        "line 3, column 8"
    ) in result.output
    assert "Traceback" not in result.output
    mock_get_helm_version.assert_called_once_with()
