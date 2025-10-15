# User Documentation and Changelog Process

User documentation and the user facing changelog are hosted in the [doc repo][docs_repo] and published to https://docs.openchatstudio.com/.

In principle, all user-facing changes should be accompanied by documentation updates and a changelog but discretion should be used. For example, if a change is purely internal and doesn't affect the user experience, it may not need to be included or if it is a very minor change.

## Changelog process

Ideally, when creating a PR, also create a PR in the [docs repo][docs_repo] with any documentation updates and a changelog entry and then add a link to the docs PR in your code changes PR.

Changelog entries should be brief but should link to any relevant documentation for further details.

### Changelog summaries

Once a week (currently on a Monday) a GitHub actions workflow runs and generates a [release](https://github.com/dimagi/open-chat-studio-docs/releases) in the docs repo with a summary of the changes since the previous release.
This creates a way for users to get notified of changes by subscribing to the release feed.

The automated releases are created in `draft` state which allows a developer to review the generated text before publishing. The releases should contain the following sections:

* New Features: new features added to the prodcut
* Improvements: changes to existing features that don't classify as 'new features'
* Bug Fixes

It should not contain a top level summary, upgrade recommendations, etc.

The process for manually reviewing and publishing a release is:

1. Review the repo diff between this release and the previous release. You can access this by using the 'Compare' drop down in the left sidebar. This is a good idea to ensure that the release notes are accurate and complete.
2. Review the previous release notes to see if there are any items that have already been included in a previous release.
3. If there are docs to link to for any item, ensure that they are added.
4. If you think there should be docs where there aren't, either create them immediately or create a ticket to be prioritized later.

Once you are happy with the release notes, publish the release. This will send a notification to all users who are subscribed to the release feed.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs

## API Documentation

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

### API Schema updates

Whenever changes are made that impact the API schema, the `api-schema.yml` file must also be updated. This is enforced by a test which will fail if the schema file is out of date. Ensuring that this file is up to date also allows us to it as a trigger for updating the API docs in the docs repo:

1. `api-schema.yml` file changes in the `main` branch.
2. `api-schema-dispatch.yml` GitHub action runs which sends a dispatch event to the OCS docs repo.
3. A GitHub action in the OCS docs repo runs and creates a PR with any updated API docs.
