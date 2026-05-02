# Manifest Builder

Generate materialized Kubernetes manifests from various types of configuration

## Installation

To install or upgrade to the latest version:

```bash
uv pip install --upgrade --pre --extra-index-url https://packages.buildkite.com/nresare/python/pypi/simple manifest-builder
```

This includes nightly builds of development versions (e.g., `0.2.1.dev6+...`).

## Development

This project is using [uv](https://docs.astral.sh/uv/) for development. To set up your dev environment,
run `uv sync`. Tests and checks can be run with the following commands:

- `uv run ruff check`
- `uv run ruff format --check`
- `uv run ty check`
- `uv run pytest`

## Requirements

- Python 3.14+
- Helm 3.x (must be installed and available in PATH)
- Git (required for `--create-commit` feature)

## Python API

Use `manifest_builder.generate` to generate manifests from Python:

```python
from pathlib import Path

from manifest_builder import generate

written_paths = generate(Path("conf"), Path("output"))
```

## Externally-owned namespaces

When the output repository is shared with other services or pipelines that
make their own commits, manifest-builder can be told which namespace
directories it does not own. Files in those directories are left alone during
cleanup, and generation fails fast if any output would land in one of them.

To declare ownership, add an `owners/` directory to your config directory and
drop one or more TOML files into it. Each file may set either of:

```toml
# A single namespace owned by another pipeline:
namespace = "team-a"

# Or a list of namespaces:
namespaces = ["monitoring", "logging"]
```

Both keys may appear in the same file, and entries from all `owners/*.toml`
files are merged into a single set of externally-owned namespaces.

## License

MIT
