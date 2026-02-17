# Manifest Builder

Generate materialized Kubernetes manifests from Helm charts.

## Features

- Parse TOML configuration files defining Helm charts to render
- Execute `helm template` for each configured chart
- Write generated manifests to an organized output directory structure
- Support for multiple values files per chart
- Dry-run mode to preview commands
- Verbose output for debugging

## Installation

```bash
# Install the package
uv pip install -e .

# Install with dev dependencies
uv pip install -e ".[dev]"
```

## Usage

### Basic Usage

```bash
# Generate manifests from conf/ directory
manifest-builder

# Or run directly with Python
python main.py
```

### Configuration

Create TOML configuration files in the `conf/` directory:

```toml
# conf/charts.toml

[[chart]]
name = "nginx-ingress"
namespace = "ingress-system"
chart = "oci://ghcr.io/nginxinc/charts/nginx-ingress"
version = "1.1.0"
values = []

[[chart]]
name = "myapp"
namespace = "default"
chart = "./charts/myapp"
values = ["values/myapp/base.yaml", "values/myapp/prod.yaml"]
```

### CLI Options

```bash
# Custom config directory
manifest-builder --config-dir ./my-configs

# Custom output directory
manifest-builder --output-dir ./manifests

# Verbose output
manifest-builder --verbose

# Generate specific charts only
manifest-builder --charts nginx-ingress,myapp

# Clean output directory before generating
manifest-builder --clean

# Combine options
manifest-builder --verbose --charts myapp --clean
```

### Output Structure

Generated manifests follow this structure:

```
output/
└── <namespace>/
    └── <release-name>.yaml
```

Example:
```
output/
├── ingress-system/
│   └── nginx-ingress.yaml
└── default/
    └── myapp.yaml
```

## Development

### Code Quality

Run quality checks:

```bash
# Lint with ruff
uv run ruff check .

# Format check
uv run ruff format --check .

# Type check with ty
uv run ty check

# Format code
uv run ruff format .
```

### Project Structure

```
manifest-builder/
├── conf/                      # TOML configuration files
├── output/                    # Generated manifests (gitignored)
├── src/
│   └── manifest_builder/
│       ├── __init__.py
│       ├── cli.py            # CLI interface
│       ├── config.py         # TOML parsing
│       ├── generator.py      # Orchestration
│       └── helm.py           # Helm command execution
├── main.py                   # Direct Python entry point
└── pyproject.toml
```

## Requirements

- Python 3.14+
- Helm 3.x (must be installed and available in PATH)

## License

MIT
