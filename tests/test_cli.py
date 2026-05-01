# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for the command-line interface."""

from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from manifest_builder.cli import main, show_diff


def test_diff_and_create_commit_are_mutually_exclusive() -> None:
    """The diff mode is an alternative to commit creation, not an add-on."""
    result = CliRunner().invoke(main, ["--diff", "--create-commit"])

    assert result.exit_code == 1
    assert "Use only one of --create-commit or --diff." in result.output


@mock.patch("manifest_builder.cli.generate_manifests")
@mock.patch("manifest_builder.cli.is_git_checkout", return_value=False)
def test_create_commit_requires_output_git_checkout(
    mock_is_git_checkout: mock.Mock,
    mock_generate_manifests: mock.Mock,
) -> None:
    """Commit creation fails fast when the output directory is not a git checkout."""
    result = CliRunner().invoke(
        main,
        ["--output-dir", "/tmp/out", "--create-commit"],
    )

    assert result.exit_code == 1
    assert (
        "It doesn't seem like /tmp/out is a git checkout, "
        "a requirement to be able to generate a commit."
    ) in result.output
    mock_is_git_checkout.assert_called_once_with(Path("/tmp/out"))
    mock_generate_manifests.assert_not_called()


@mock.patch("manifest_builder.cli.generate_manifests")
@mock.patch("manifest_builder.cli.is_git_checkout", return_value=False)
def test_diff_requires_output_git_checkout(
    mock_is_git_checkout: mock.Mock,
    mock_generate_manifests: mock.Mock,
) -> None:
    """Diff mode fails fast when the output directory is not a git checkout."""
    result = CliRunner().invoke(
        main,
        ["--output-dir", "/tmp/out", "--diff"],
    )

    assert result.exit_code == 1
    assert (
        "It doesn't seem like /tmp/out is a git checkout, "
        "a requirement to be able to generate a diff."
    ) in result.output
    mock_is_git_checkout.assert_called_once_with(Path("/tmp/out"))
    mock_generate_manifests.assert_not_called()


@mock.patch("manifest_builder.cli.get_manifest_diff", return_value="")
@mock.patch("manifest_builder.cli.generate_manifests", return_value=set())
@mock.patch("manifest_builder.cli.load_images", return_value={})
@mock.patch("manifest_builder.cli.resolve_configs", return_value=[])
@mock.patch("manifest_builder.cli.load_configs", return_value=[])
@mock.patch("manifest_builder.cli.get_helm_version", return_value="v3.0.0")
@mock.patch("manifest_builder.cli.is_git_dirty", return_value=False)
@mock.patch("manifest_builder.cli.is_git_checkout", return_value=True)
def test_diff_reports_when_output_is_identical(
    mock_is_git_checkout: mock.Mock,
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
    mock_is_git_checkout.assert_any_call(Path.cwd() / "output")
    mock_get_manifest_diff.assert_called_once_with(Path.cwd() / "output", set(), set())


@mock.patch("manifest_builder.cli.get_manifest_diff", return_value="diff output")
@mock.patch(
    "manifest_builder.cli.generate_manifests", return_value={Path("/out/app.yaml")}
)
@mock.patch("manifest_builder.cli.load_owned_namespaces", return_value={"owned"})
@mock.patch("manifest_builder.cli.load_images", return_value={"app": "image"})
@mock.patch("manifest_builder.cli.resolve_configs", return_value=["resolved"])
@mock.patch("manifest_builder.cli.load_configs", return_value=["loaded"])
@mock.patch("manifest_builder.cli.load_helmfile", return_value=None)
@mock.patch("manifest_builder.cli.is_git_dirty", return_value=False)
@mock.patch("manifest_builder.cli.is_git_checkout", return_value=True)
def test_show_diff_returns_generated_manifest_diff(
    mock_is_git_checkout: mock.Mock,
    mock_is_git_dirty: mock.Mock,
    mock_load_helmfile: mock.Mock,
    mock_load_configs: mock.Mock,
    mock_resolve_configs: mock.Mock,
    mock_load_images: mock.Mock,
    mock_load_owned_namespaces: mock.Mock,
    mock_generate_manifests: mock.Mock,
    mock_get_manifest_diff: mock.Mock,
    tmp_path: Path,
) -> None:
    """The reusable diff function returns the generated git diff as a string."""
    config = tmp_path / "config"
    output = tmp_path / "output"
    config.mkdir()
    output.mkdir()
    (config / "releases.yaml").write_text("releases: []\n")

    result = show_diff(config, output)

    assert result == "diff output"
    mock_is_git_checkout.assert_called_once_with(output)
    mock_is_git_dirty.assert_called_once_with(config)
    mock_load_helmfile.assert_called_once_with(config / "releases.yaml")
    mock_load_configs.assert_called_once()
    mock_resolve_configs.assert_called_once_with(["loaded"], None)
    mock_load_images.assert_called_once_with(config)
    mock_load_owned_namespaces.assert_called_once_with(config)
    mock_generate_manifests.assert_called_once_with(
        configs=["resolved"],
        output_dir=output,
        repo_root=Path.cwd(),
        images={"app": "image"},
        verbose=False,
        owned_namespaces={"owned"},
    )
    mock_get_manifest_diff.assert_called_once_with(
        output,
        {Path("/out/app.yaml")},
        {"owned"},
    )
