# Code Quality Standards for manifest-builder

This document outlines the development practices and tooling requirements for this project when working with Claude Code and other AI assistants.

## Code Quality Checks

After every code change, run the following checks to ensure code quality and consistency:

```bash
uv run ruff check
uv run ruff format --check
uv run ty check
uv run pytest
```

### Individual Tool Purposes

- **`ruff check`** - Lints code for style violations and potential bugs
- **`ruff format --check`** - Verifies code is properly formatted according to project standards
- **`ty check`** - Type checks the code (equivalent to `mypy`)
- **`pytest`** - Runs the test suite to ensure functionality is correct

## Writing Tests

When implementing new features or fixing bugs:

1. **Add proper test cases** to the test suite rather than using ad-hoc verification scripts
2. **Test bundled templates** by using the actual bundled templates (not `_templates_override`) to validate production code paths
3. **Follow existing test patterns** - new tests should match the style and structure of existing tests
4. **Use clear docstrings** to document what each test validates
5. **Test edge cases** - include tests for expected behavior with various inputs (strings, lists, None/empty values)

## Project Structure

- **Source code**: `src/manifest_builder/`
- **Tests**: `tests/` (run with `pytest`)
- **Templates**: `src/manifest_builder/templates/web/` (Mustache templates for Kubernetes resources)
- **Configuration**: `pyproject.toml` (project metadata and tool configuration)

## Dependencies

Development is managed with [uv](https://docs.astral.sh/uv/). Install dependencies with:

```bash
uv sync
```

## Making Commits

Only create commits when explicitly requested by the user.

**Commit message format:**
- **First line** (subject): Brief, descriptive summary (keep it short)
- **Body**: One to two sentences explaining what was changed and why
- Include the co-author footer: `Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>`

Example:
```
Propagate args to Deployment container and simplify extra_hostnames

Add args field to container spec with support for string/list formats.
Simplify extra_hostnames to use ExtraHostname class and add test coverage.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```
