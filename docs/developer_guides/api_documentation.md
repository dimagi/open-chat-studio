# API Documentation

The OCS REST API is primarily documented via its OpenAPI schema. The schema is created using [drf-spectacular](https://drf-spectacular.readthedocs.io/en/latest/).

The current production schema is available at https://chatbots.dimagi.com/api/schema/. It is also kept in the code repository in the `api-schema.yml` file. This file serves two purposes:

1. Provide an easy way to visually inspect changes to the schema.
2. Provide a reference for generating API documentation in the docs repo (see below).

The schema can be generated locally by running:

```bash
inv schema
# OR
python manage.py spectacular --file api-schema.yml --validate
```

## API Schema updates

Whenever changes are made that impact the API schema, the `api-schema.yml` file must also be updated. This is enforced by a test which will fail if the schema file is out of date. Ensuring that this file is up to date also allows us to use it as a trigger for updating the API docs in the docs repo:

1. `api-schema.yml` file changes in the `main` branch.
2. `api-schema-dispatch.yml` GitHub action runs which sends a dispatch event to the OCS docs repo.
3. A GitHub action in the OCS docs repo runs and creates a PR with any updated API docs.
