# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for the reusable manifest-builder API."""

from pathlib import Path
from unittest import mock

from manifest_builder import generate
from manifest_builder.api import generate as api_generate


def test_generate_is_available_from_top_level_package() -> None:
    """Call sites can import generate directly from manifest_builder."""
    assert generate.__name__ == "generate"


@mock.patch("manifest_builder.api.generate_manifests")
@mock.patch("manifest_builder.api.is_git_checkout", return_value=False)
def test_create_commit_requires_output_git_checkout(
    mock_is_git_checkout: mock.Mock,
    mock_generate_manifests: mock.Mock,
) -> None:
    """Commit creation fails fast when the output directory is not a git checkout."""
    output = Path("/tmp/out")

    try:
        api_generate(Path("conf"), output, create_commit=True)
    except ValueError as e:
        error = str(e)
    else:
        raise AssertionError("generate() should reject non-git commit output")

    assert (
        "It doesn't seem like /tmp/out is a git checkout, "
        "a requirement to be able to generate a commit."
    ) == error
    mock_is_git_checkout.assert_called_once_with(output)
    mock_generate_manifests.assert_not_called()


@mock.patch(
    "manifest_builder.api.generate_manifests", return_value={Path("/out/app.yaml")}
)
@mock.patch("manifest_builder.api.load_owned_namespaces", return_value={"owned"})
@mock.patch("manifest_builder.api.load_images", return_value={"app": "image"})
@mock.patch("manifest_builder.api.resolve_configs", return_value=["resolved"])
@mock.patch("manifest_builder.api.load_configs", return_value=["loaded"])
@mock.patch("manifest_builder.api.load_helmfile", return_value=None)
def test_generate_accepts_config_and_output_paths(
    mock_load_helmfile: mock.Mock,
    mock_load_configs: mock.Mock,
    mock_resolve_configs: mock.Mock,
    mock_load_images: mock.Mock,
    mock_load_owned_namespaces: mock.Mock,
    mock_generate_manifests: mock.Mock,
    tmp_path: Path,
) -> None:
    """The reusable generation function accepts config and output Paths."""
    config = tmp_path / "config"
    output = tmp_path / "output"
    config.mkdir()
    output.mkdir()
    (config / "releases.yaml").write_text("releases: []\n")

    result = api_generate(config, output, repo_root=tmp_path)

    assert result == {Path("/out/app.yaml")}
    mock_load_helmfile.assert_called_once_with(config / "releases.yaml")
    mock_load_configs.assert_called_once()
    mock_resolve_configs.assert_called_once_with(["loaded"], None)
    mock_load_images.assert_called_once_with(config)
    mock_load_owned_namespaces.assert_called_once_with(config)
    mock_generate_manifests.assert_called_once_with(
        handlers=["resolved"],
        output_dir=output,
        repo_root=tmp_path,
        images={"app": "image"},
        verbose=False,
        owned_namespaces={"owned"},
    )


@mock.patch("manifest_builder.api.generate", return_value={Path("/out/app.yaml")})
def test_top_level_generate_delegates_to_api(mock_generate: mock.Mock) -> None:
    """The top-level convenience import calls the API implementation."""
    result = generate(
        Path("conf"),
        Path("output"),
        repo_root=Path("/repo"),
        verbose=True,
        create_commit=True,
        allow_dirty_config=True,
    )

    assert result == {Path("/out/app.yaml")}
    mock_generate.assert_called_once_with(
        Path("conf"),
        Path("output"),
        Path("/repo"),
        True,
        True,
        True,
    )
