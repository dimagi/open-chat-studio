# API Documentation

The OCS REST API is primarily documented via its OpenAPI schema. The schema is created using [drf-spectacular](https://drf-spectacular.readthedocs.io/en/latest/).

The current production schema is available at https://www.openchatstudio.com/api/schema/. It is also kept in the code repository, one file per API surface: `api-schemas/v1.yml`, `api-schemas/v2.yml` and `api-schemas/export.yml`. These files serve two purposes:

- Provide an easy way to visually inspect changes to the schema.
- Provide a reference for generating API documentation in the docs repo (see below).

The schemas can be generated locally by running:

```bash
inv schema
# OR, for one surface; the empty key keeps a local OIDC setup's scopes out of the file, as inv schema does
OIDC_RSA_PRIVATE_KEY='' python manage.py spectacular --api-version v2 --file api-schemas/v2.yml --validate
```

## API Schema updates

Whenever changes are made that impact the API schema, the files under `api-schemas/` must also be updated. This is enforced by a test which will fail if a schema file is out of date. On a pull request from a branch in this repository, the `update-generated-files.yml` workflow regenerates the files and commits them to the branch. Ensuring that these files are up to date also allows us to use them as a trigger for updating the API docs in the docs repo:

1. A file under `api-schemas/` changes in the `main` branch.
2. `api-schema-dispatch.yml` GitHub action runs which sends a dispatch event to the OCS docs repo.
3. A GitHub action in the OCS docs repo runs and creates a PR with any updated API docs.

This is one of two ways docs/changelog updates get triggered from a merged PR — see [User Documentation and Changelog Process](index.md) for the other (manual PR-description-driven) path and how the two relate.

## Schema linting

`.spectral.yaml` at the repository root holds the [Spectral](https://github.com/stoplightio/spectral) rules the schemas are held to, on top of the stock `spectral:oas` set: every example matches its schema, every tag an operation uses is declared, operationIds are `snake_case`, every operation declares `security`, and v2 operations carry a description. The `spectral` pre-commit hook runs them whenever a schema file is in a commit or in a pull request's changes. To run it by hand:

```bash
prek run spectral --all-files
```

A finding is fixed at its source, never in the schema file, which the up-to-date test would reject anyway:

- An example that does not match its schema: the `OpenApiExample` in the view's `extend_schema` call, or the serializer it describes.
- A missing description: `extend_schema(description=...)` on the operation.
- An undeclared tag, or a gap in the `info` object: `SPECTACULAR_SETTINGS` in `config/settings.py`.
- A change every schema needs: a hook in `apps/api/schema.py`.

Then regenerate with `inv schema` and run the hook again.
