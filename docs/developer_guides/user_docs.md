# User Documentation and Changelog Process

## Overview

OCS uses a Docs-as-Code + LLM Augmentation approach for user documentation: the source of truth for [user-facing docs](https://docs.openchatstudio.com/) and the [user-facing changelog](https://docs.openchatstudio.com/changelog/) live in version-controlled files in the [docs repo][docs_repo], and follow the same PR/review workflow as product code.

LLM-based automation (with Claude) helps draft changelog entries **and** user documentation updates from merged PRs, while developers still decide when changes are user-facing, provide context in the PR, and review generated output before publishing. [Weekly release notes](release_notes.md) are then automatically published as [GitHub releases](https://github.com/dimagi/open-chat-studio-docs/releases).

All user-facing changes should ideally be accompanied by documentation and changelog updates. However, use discretion: purely internal changes or very minor updates may not require docs. In general, treat documentation as part of the feature — this avoids shipping UI that points users to outdated or missing documentation.

The PR template has two independent checkboxes, for two audiences:

| Checkbox | Audience | Where the entry goes |
|---|---|---|
| "This PR requires docs/changelog update" | product users | docs repo, written for you by the automation |
| "Self-hosted operators must know about or act on this change" | self-host operators | `CHANGELOG.md` at the root of this repo, written by you in the PR |

Most user-facing PRs need only the first. Check the second when an operator has
to *do* something on upgrade — a migration, a new or changed setting, a change
in deployment shape, a deprecation or removal, or a security fix that needs
operator action such as rotating a credential. Some PRs need both, and a purely
internal migration needs only the second. See [`RELEASING.md`](https://github.com/dimagi/open-chat-studio/blob/main/RELEASING.md)
for how those entries are cut into a tagged release.

The rest of this page covers the first checkbox, the user-facing changelog.

## What to do in your PR

Check the **"This PR requires docs/changelog update"** checkbox, then:

- Add notes in the PR description to help the automation write accurate changelog and user docs content.
- If your PR touches the chat widget (files under `components/`), keep widget and main app changes in separate PRs — a PR touching both is treated as a widget change, and only the widget changelog gets updated. Include the widget version number in the PR description (e.g. "v0.4.9").
- Changelog entries should be brief but should link to any relevant documentation for further details.

That's it for most PRs — merging picks up the automatic changelog update and opens a docs PR on your behalf. See [Changelog Automation](changelog_automation.md) for how that works internally, and what to do if it doesn't fire for your PR.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs
