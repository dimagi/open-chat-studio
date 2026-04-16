# User Documentation and Changelog Process

User documentation, the [user-facing changelog](https://docs.openchatstudio.com/changelog/) and weekly release notes are hosted in the [doc repo][docs_repo] and published to https://docs.openchatstudio.com/.

In principle, all user-facing changes should be accompanied by user documentation updates and a changelog but discretion should be used. For example, if a change is purely internal and doesn't affect the user experience, it may not need to be included or if it is a very minor change.

## Changelog process

The easiest way to trigger a docs/changelog update is to check the **"This PR requires docs/changelog update"** checkbox in the PR description.

### Automatic creation of Changelog entries
The [automation](https://github.com/dimagi/open-chat-studio/blob/main/.github/workflows/docs-changelog-dispatch.yml) runs when a PR targeting `main` that touches files under `apps/`, `components/`, `config/`, `assets/`, or `templates/` is merged with the PR description box checked — it will then analyse the changes and open a PR in the [docs repo][docs_repo] with a changelog entry on your behalf.

You can add notes in the PR description to help the automation write accurate changelog and docs content. Changelog entries should be brief but should link to any relevant documentation for further details.

**Note**: PRs that don't touch the paths above (e.g. tech docs-only changes) will not trigger the automation. Use the manual option below in those cases.

### Manual Option
Alternatively, you can create a Changelog manually yourself: open a PR in the [docs repo][docs_repo] with any updates that users must be aware of and a [changelog entry](https://github.com/dimagi/open-chat-studio-docs/blob/main/docs/changelog.md), and then link it from the code PR.


## Weekly Release Notes from Changelog summaries

Once a week (currently on a Monday) a [GitHub Actions workflow](https://github.com/dimagi/open-chat-studio-docs/blob/main/.github/workflows/release.yml) runs and generates a [release note](https://github.com/dimagi/open-chat-studio-docs/releases) in the GitHub **docs repo** with a summary of the changes since the previous release.
This creates a way for users to get notified of changes by subscribing to the release feed of the docs repo.

The automated releases are created in `draft` state which allows a developer to review the generated text before publishing. The releases should contain the following sections:

* New Features: new features added to the product
* Improvements: changes to existing features that don't classify as 'new features'
* Bug Fixes

It should not contain a top level summary, upgrade recommendations, etc.

### Review and Publish Release Note
The process for manually reviewing and publishing a release is:

1. Review the repo diff between this release and the previous release. You can access this by using the 'Compare' drop down in the left sidebar. This is a good idea to ensure that the release notes are accurate and complete.
2. Review the previous release notes to see if there are any items that have already been included in a previous release.
3. If there are user docs to link to for any item, ensure that they are added.
4. If you think there should be docs where there aren't, either create them immediately or create a [ticket](https://github.com/dimagi/open-chat-studio-docs/issues) to be prioritized later.

Once you are happy with the release notes, publish the release. This will send a notification to all users who are subscribed to the docs release feed.

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

Whenever changes are made that impact the API schema, the `api-schema.yml` file must also be updated. This is enforced by a test which will fail if the schema file is out of date. Ensuring that this file is up to date also allows us to use it as a trigger for updating the API docs in the docs repo:

1. `api-schema.yml` file changes in the `main` branch.
2. `api-schema-dispatch.yml` GitHub action runs which sends a dispatch event to the OCS docs repo.
3. A GitHub action in the OCS docs repo runs and creates a PR with any updated API docs.
