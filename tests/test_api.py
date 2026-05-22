# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Tests for the reusable manifest-builder API."""

from pathlib import Path
from unittest import mock
from unittest.mock import call

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
    mock_load_configs.assert_called_once_with(
        config,
        mock.ANY,
        extra_variables=None,
        default_namespace=None,
        default_image=None,
    )
    mock_resolve_configs.assert_called_once_with(["loaded"], None)
    mock_load_images.assert_called_once_with(config)
    mock_load_owned_namespaces.assert_has_calls([call(config), call(output)])
    mock_generate_manifests.assert_called_once_with(
        handlers=["resolved"],
        output_dir=output,
        repo_root=tmp_path,
        images={"app": "image"},
        verbose=False,
        owned_namespaces={"owned"},
        managed_namespaces=None,
    )


@mock.patch("manifest_builder.api.generate_manifests")
@mock.patch("manifest_builder.api.load_owned_namespaces", return_value=set())
@mock.patch("manifest_builder.api.load_images", return_value={})
@mock.patch("manifest_builder.api.resolve_configs", return_value=["resolved"])
@mock.patch("manifest_builder.api.load_configs", return_value=["loaded"])
@mock.patch("manifest_builder.api.load_helmfile", return_value=None)
def test_generate_namespace_mode_writes_owner_file(
    mock_load_helmfile: mock.Mock,
    mock_load_configs: mock.Mock,
    mock_resolve_configs: mock.Mock,
    mock_load_images: mock.Mock,
    mock_load_owned_namespaces: mock.Mock,
    mock_generate_manifests: mock.Mock,
    tmp_path: Path,
) -> None:
    """Namespace mode declares ownership in the output owners directory."""
    config = tmp_path / "config"
    output = tmp_path / "output"
    config.mkdir()
    output.mkdir()
    mock_generate_manifests.return_value = {output / "team-a" / "deployment-app.yaml"}

    result = api_generate(config, output, repo_root=tmp_path, namespace="team-a")

    owner = output / "owners" / "team-a.toml"
    assert owner in result
    assert owner.read_text() == 'namespace = "team-a"\n'
    mock_load_configs.assert_called_once_with(
        config,
        mock.ANY,
        extra_variables=None,
        default_namespace="team-a",
        default_image=None,
    )
    mock_load_owned_namespaces.assert_has_calls([call(config), call(output)])
    mock_generate_manifests.assert_called_once_with(
        handlers=["resolved"],
        output_dir=output,
        repo_root=tmp_path,
        images={},
        verbose=False,
        owned_namespaces=set(),
        managed_namespaces={"team-a"},
    )


@mock.patch("manifest_builder.api.generate_manifests")
@mock.patch("manifest_builder.api.load_owned_namespaces", return_value=set())
@mock.patch("manifest_builder.api.load_images", return_value={})
@mock.patch("manifest_builder.api.resolve_configs", return_value=["resolved"])
@mock.patch("manifest_builder.api.load_configs", return_value=["loaded"])
@mock.patch("manifest_builder.api.load_helmfile", return_value=None)
def test_generate_namespace_mode_passes_image_default(
    mock_load_helmfile: mock.Mock,
    mock_load_configs: mock.Mock,
    mock_resolve_configs: mock.Mock,
    mock_load_images: mock.Mock,
    mock_load_owned_namespaces: mock.Mock,
    mock_generate_manifests: mock.Mock,
    tmp_path: Path,
) -> None:
    """The API image parameter is passed as a namespace-mode config default."""
    del (
        mock_load_helmfile,
        mock_resolve_configs,
        mock_load_images,
        mock_load_owned_namespaces,
    )
    config = tmp_path / "config"
    output = tmp_path / "output"
    config.mkdir()
    output.mkdir()
    mock_generate_manifests.return_value = {output / "team-a" / "deployment-app.yaml"}

    api_generate(
        config,
        output,
        repo_root=tmp_path,
        namespace="team-a",
        image="registry.example.com/app:1.0",
    )

    mock_load_configs.assert_called_once_with(
        config,
        mock.ANY,
        extra_variables=None,
        default_namespace="team-a",
        default_image="registry.example.com/app:1.0",
    )


def test_generate_image_requires_namespace(tmp_path: Path) -> None:
    """The API image parameter only has meaning in namespace mode."""
    try:
        api_generate(
            tmp_path / "config",
            tmp_path / "output",
            repo_root=tmp_path,
            image="registry.example.com/app:1.0",
        )
    except ValueError as e:
        error = str(e)
    else:
        raise AssertionError("generate() should reject image without namespace")

    assert error == "generate(image=...) can only be used when namespace is set"


@mock.patch("manifest_builder.api.generate_manifests")
@mock.patch("manifest_builder.api.load_owned_namespaces", return_value=set())
@mock.patch("manifest_builder.api.load_images", return_value={})
@mock.patch("manifest_builder.api.resolve_configs", return_value=["resolved"])
@mock.patch("manifest_builder.api.load_configs", return_value=["loaded"])
@mock.patch("manifest_builder.api.load_helmfile", return_value=None)
def test_generate_namespace_mode_rejects_cluster_output(
    mock_load_helmfile: mock.Mock,
    mock_load_configs: mock.Mock,
    mock_resolve_configs: mock.Mock,
    mock_load_images: mock.Mock,
    mock_load_owned_namespaces: mock.Mock,
    mock_generate_manifests: mock.Mock,
    tmp_path: Path,
) -> None:
    """Namespace mode fails when any generated file lands in cluster/."""
    del (
        mock_load_helmfile,
        mock_load_configs,
        mock_resolve_configs,
        mock_load_images,
        mock_load_owned_namespaces,
    )
    config = tmp_path / "config"
    output = tmp_path / "output"
    config.mkdir()
    output.mkdir()
    mock_generate_manifests.return_value = {output / "cluster" / "clusterrole-app.yaml"}

    try:
        api_generate(config, output, repo_root=tmp_path, namespace="team-a")
    except ValueError as e:
        error = str(e)
    else:
        raise AssertionError("generate() should reject cluster output")

    assert "--namespace mode cannot generate cluster-scoped manifests" in error
    assert not (output / "owners" / "team-a.toml").exists()


@mock.patch("manifest_builder.api.create_manifest_commit")
@mock.patch("manifest_builder.api.get_git_commit", return_value="abc123")
@mock.patch("manifest_builder.api.is_git_dirty", return_value=False)
@mock.patch("manifest_builder.api.is_git_checkout", return_value=True)
@mock.patch("manifest_builder.api.generate_manifests")
@mock.patch("manifest_builder.api.load_owned_namespaces", return_value=set())
@mock.patch("manifest_builder.api.load_images", return_value={})
@mock.patch("manifest_builder.api.resolve_configs", return_value=["resolved"])
@mock.patch("manifest_builder.api.load_configs", return_value=["loaded"])
@mock.patch("manifest_builder.api.load_helmfile", return_value=None)
def test_namespace_mode_commit_preserves_non_target_directories(
    mock_load_helmfile: mock.Mock,
    mock_load_configs: mock.Mock,
    mock_resolve_configs: mock.Mock,
    mock_load_images: mock.Mock,
    mock_load_owned_namespaces: mock.Mock,
    mock_generate_manifests: mock.Mock,
    mock_is_git_checkout: mock.Mock,
    mock_is_git_dirty: mock.Mock,
    mock_get_git_commit: mock.Mock,
    mock_create_manifest_commit: mock.Mock,
    tmp_path: Path,
) -> None:
    """Commit cleanup also treats non-target output directories as protected."""
    del (
        mock_load_helmfile,
        mock_load_configs,
        mock_resolve_configs,
        mock_load_images,
        mock_load_owned_namespaces,
        mock_is_git_checkout,
        mock_is_git_dirty,
        mock_get_git_commit,
    )
    config = tmp_path / "config"
    output = tmp_path / "output"
    config.mkdir()
    (output / "team-a").mkdir(parents=True)
    (output / "team-b").mkdir()
    (output / "cluster").mkdir()
    generated = output / "team-a" / "deployment-app.yaml"
    mock_generate_manifests.return_value = {generated}

    result = api_generate(
        config,
        output,
        repo_root=tmp_path,
        namespace="team-a",
        create_commit=True,
    )

    assert generated in result
    mock_create_manifest_commit.assert_called_once()
    assert mock_create_manifest_commit.call_args.args[4] == {"team-b", "cluster"}


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


@mock.patch("manifest_builder.api.generate", return_value={Path("/out/app.yaml")})
def test_top_level_generate_passes_namespace_image(mock_generate: mock.Mock) -> None:
    """The top-level convenience wrapper exposes the image override."""
    result = generate(
        Path("conf"),
        Path("output"),
        repo_root=Path("/repo"),
        namespace="team-a",
        image="registry.example.com/app:1.0",
    )

    assert result == {Path("/out/app.yaml")}
    mock_generate.assert_called_once_with(
        Path("conf"),
        Path("output"),
        Path("/repo"),
        False,
        False,
        False,
        namespace="team-a",
        image="registry.example.com/app:1.0",
    )
