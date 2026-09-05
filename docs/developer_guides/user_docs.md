# User Documentation and Changelog Process

OCS uses a Docs-as-Code + LLM Augmentation approach for user documentation: the source of truth for [user-facing docs](https://docs.openchatstudio.com/) and the [user-facing changelog](https://docs.openchatstudio.com/changelog/) live in version-controlled files in the [docs repo][docs_repo], and follow the same PR/review workflow as product code.

LLM-based automation (with Claude) helps draft changelog entries **and** user documentation updates from merged PRs, while developers still decide when changes are user-facing, provide context in the PR, and review generated output before publishing.

[Weekly release notes](release_notes.md) are automatically published as [GitHub releases](https://github.com/dimagi/open-chat-studio-docs/releases).

## Guidelines

All user-facing changes should ideally be accompanied by documentation and changelog updates. However, use discretion: purely internal changes or very minor updates may not require docs. In general, treat documentation as part of the feature — this avoids shipping UI that points users to outdated or missing documentation.


## Two changelogs, two checkboxes

The PR template has two independent checkboxes, for two audiences:

| Checkbox | Audience | Where the entry goes |
|---|---|---|
| "This PR requires docs/changelog update" | product users | docs repo, written for you by the automation below |
| "Self-hosted operators must know about or act on this change" | self-host operators | `CHANGELOG.md` at the root of this repo, written by you in the PR |

Most user-facing PRs need only the first. Check the second when an operator has
to *do* something on upgrade — a migration, a new or changed setting, a change
in deployment shape, a deprecation or removal, or a security fix that needs
operator action such as rotating a credential. Some PRs need both, and a purely
internal migration needs only the second. See [`RELEASING.md`](https://github.com/dimagi/open-chat-studio/blob/main/RELEASING.md)
for how those entries are cut into a tagged release.

The rest of this page covers the user-facing changelog.

## Changelog process

The easiest way to trigger a docs/changelog update is to check the **"This PR requires docs/changelog update"** checkbox in the PR description.

### Automatic creation of documentation updates and changelog entries
The [dispatch workflow](https://github.com/dimagi/open-chat-studio/blob/main/.github/workflows/docs-changelog-dispatch.yml) runs when a PR targeting `main` that touches files under `apps/`, `components/`, `config/`, `assets/`, or `templates/` is merged with the PR description box checked. It sends a dispatch event to the [docs repo][docs_repo], which then uses Claude AI to analyze the changes and open a PR with documentation updates and a changelog entry on your behalf.

#### Widget vs. Main App changes

The automation handles **chat widget** changes (files under `components/`) differently from main app changes:

| Change type | Changelog file | PR base branch |
|---|---|---|
| Main App | `docs/changelog.md` | `main` |
| Widget (`components/`) | `docs/chat_widget/changelog.md` | `widget-develop` |

- If a PR touches **both** widget and main app files, it is treated as a widget change and only the widget changelog is updated.
- Keep widget and main app changes in separate PRs to ensure both changelogs are updated.
- You can add notes in the PR description to help the automation write accurate changelog and docs content.
- Changelog entries should be brief but should link to any relevant documentation for further details. For widget releases, include the version number in the PR description (e.g. "v0.4.9").
- **Note**: PRs that don't touch the paths above (e.g. tech docs-only changes) will not trigger the automation. Use the manual option below in those cases.

### Manual trigger in docs repo
The [update-changelog workflow](https://github.com/dimagi/open-chat-studio-docs/actions/workflows/update-changelog.yml) in the docs repo can also be triggered manually: go to **GitHub Actions → "Update Changelog and Docs from OCS PR"** and enter the OCS PR number.

### Manual option
Alternatively, you can update the main changelog or widget changelog as appropriate (see above).

## API Documentation

See the [API Documentation guide](api_documentation.md) for information on how the OCS REST API is documented, how to generate the schema locally, and what to do when your changes affect the API schema.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs
