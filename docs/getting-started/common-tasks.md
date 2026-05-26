# Common Development Tasks

These commands apply to both [local](local-setup.md) and [Docker](docker-setup.md) setups unless noted.

## Running Tests

```bash
pytest
```

Or to test a specific app/module:

```bash
pytest apps/utils/tests/test_slugs.py
```

## Updating Translations

```bash
inv translations
```

## Linting and Formatting

The project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
inv ruff
```

## Updating Requirements

```bash
inv requirements
```

To add a new requirement:

```bash
uv add <package-name>

# for dev / prod dependencies
uv add <package-name> --group [dev|prod]
```
