# Manifest Builder

Generate materialized Kubernetes manifests from various types of configuration

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

## License

MIT
