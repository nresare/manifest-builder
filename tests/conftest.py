# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: The manifest-builder contributors
"""Shared test helpers."""

from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo


def init_test_repo(path: Path) -> Repo:
    """Initialize a Dulwich repo with commit signing disabled.

    The user's global git config may set ``commit.gpgsign = true``; that
    invokes gpg during ``porcelain.commit``, which fails in CI / sandboxed
    test environments. Disable it at the per-repo level so tests don't
    depend on the host's gpg setup.
    """
    repo = porcelain.init(path)
    config = repo.get_config()
    config.set((b"commit",), b"gpgsign", b"false")
    config.set((b"tag",), b"gpgsign", b"false")
    config.write_to_path()
    return repo
