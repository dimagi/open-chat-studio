# User Documentation and Changelog Process

User documentation and the [user-facing changelog](https://docs.openchatstudio.com/changelog/) are maintained in the [docs repo][docs_repo] and published to https://docs.openchatstudio.com/. Weekly release notes are published as [GitHub releases](https://github.com/dimagi/open-chat-studio-docs/releases).

## User Documentation Process

In principle, all user-facing changes should be accompanied by user documentation updates and a changelog, but discretion should be used. For example, if a change is purely internal and doesn't affect the user experience, it may not need to be included. The same applies to very minor changes.

## Changelog process

The easiest way to trigger a docs/changelog update is to check the **"This PR requires docs/changelog update"** checkbox in the PR description.

### Automatic creation of changelog entries
The [dispatch workflow](https://github.com/dimagi/open-chat-studio/blob/main/.github/workflows/docs-changelog-dispatch.yml) runs when a PR targeting `main` that touches files under `apps/`, `components/`, `config/`, `assets/`, or `templates/` is merged with the PR description box checked. It sends a dispatch event to the [docs repo][docs_repo], which then uses Claude AI to analyze the changes and open a PR with a changelog entry on your behalf.

#### Widget vs. Main App changes

The automation handles **chat widget** changes (files under `components/`) differently from main app changes:

| Change type | Changelog file | PR base branch |
|---|---|---|
| Main App | `docs/changelog.md` | `main` |
| Widget (`components/`) | `docs/chat_widget/changelog.md` | `widget-develop` |

If a PR touches **both** widget and main app files, it is treated as a widget change and only the widget changelog is updated. Keep widget and main app changes in separate PRs to ensure both changelogs are updated.

You can add notes in the PR description to help the automation write accurate changelog and docs content. Changelog entries should be brief but should link to any relevant documentation for further details. For widget releases, include the version number in the PR description (e.g. "v0.4.9").

**Note**: PRs that don't touch the paths above (e.g. tech docs-only changes) will not trigger the automation. Use the manual option below in those cases.

### Manual trigger in docs repo
The [update-changelog workflow](https://github.com/dimagi/open-chat-studio-docs/actions/workflows/update-changelog.yml) in the docs repo can also be triggered manually: go to **Actions → "Update Changelog and Docs from OCS PR"** and enter the OCS PR number.

### Manual option
Alternatively, you can create a changelog entry manually: open a PR in the [docs repo][docs_repo] with any updates that users must be aware of and create a [changelog entry](https://github.com/dimagi/open-chat-studio-docs/blob/main/docs/changelog.md), then link it from the code PR.

## Weekly release notes from changelog summaries

Once a week (currently on a Monday), a [GitHub Actions workflow](https://github.com/dimagi/open-chat-studio-docs/blob/main/.github/workflows/release.yml) runs and generates a [release note](https://github.com/dimagi/open-chat-studio-docs/releases) in the GitHub **docs repo** with a summary of the changes since the previous release.
This creates a way for users to get notified of changes by subscribing to the release feed of the docs repo.

The automated releases are created in `draft` state, which allows a developer to review the generated text before publishing. The releases should contain the following sections:

* New Features: new features added to the product
* Improvements: changes to existing features that don't classify as 'new features'
* Bug Fixes

It should not contain a top-level summary, upgrade recommendations, etc.

### Review and Publish Release Note
The process for manually reviewing and publishing a release is:

1. Review the repo diff between this release and the previous release. You can access this using the 'Compare' dropdown in the left sidebar. This is a good idea to ensure that the release notes are accurate and complete.
2. Review the previous release notes to see if there are any items that have already been included in a previous release.
3. If there are user docs to link to for any item, ensure that they are added.
4. If you think there should be docs where there aren't, either create them immediately or create a [ticket](https://github.com/dimagi/open-chat-studio-docs/issues) to be prioritized later.

Once you are happy with the release notes, publish the release. This will send a notification to all users who are subscribed to the docs release feed.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs

## API Documentation

See the [API Documentation guide](api_documentation.md) for information on how the OCS REST API is documented, how to generate the schema locally, and what to do when your changes affect the API schema.
