# User Documentation and Changelog Process

OCS uses a Docs-as-Code + LLM Augmentation approach for user documentation: the source of truth for [user-facing docs](https://docs.openchatstudio.com/) and the [user-facing changelog](https://docs.openchatstudio.com/changelog/) live in version-controlled files in the [docs repo][docs_repo], and follow the same PR/review workflow as product code.

LLM-based automation (with Claude) helps draft changelog entries **and** user documentation updates from merged PRs, while developers still decide when changes are user-facing, provide context in the PR, and review generated output before publishing.

[Weekly release notes](release_notes.md) are then automatically published as [GitHub releases](https://github.com/dimagi/open-chat-studio-docs/releases).

## When to update docs

All user-facing changes should ideally be accompanied by documentation and changelog updates. However, use discretion: purely internal changes or very minor updates may not require docs. In general, treat documentation as part of the feature — this avoids shipping UI that points users to outdated or missing documentation.

## Two changelogs, two checkboxes

The PR template has two independent checkboxes, for two audiences:

| Checkbox | Audience | Where the entry goes |
|---|---|---|
| "This PR requires docs/changelog update" | product users | docs repo, written for you by the automation |
| "Self-hosted operators must know about or act on this change" | self-host operators | `CHANGELOG.md` at the root of this repo, written by you in the PR |

Most user-facing PRs need only the first. Some need both 1 and 3 below — e.g. a user-facing feature that also requires an operator migration — and a purely internal migration needs only 3.

## What to do in your PR

Most PRs fall into one main case, plus two variants. Follow whichever applies:

### 1. Main app changes (the common case)

1. Check the **"This PR requires docs/changelog update"** checkbox.
2. Add notes in the PR description to help the automation write accurate changelog and user docs content — keep entries brief, but link to any relevant documentation for further details.
3. Merge as normal. The automation picks up the merge and opens a docs PR on your behalf — see [Changelog Automation](changelog_automation.md) for how that works internally, and what to do if it doesn't fire.

### 2. Widget changes

If your PR touches the chat widget (files under `components/`):

1. Keep widget changes in a separate PR from any main app changes — a PR touching both is treated as a widget change, and only the widget changelog gets updated.
2. Check the **"This PR requires docs/changelog update"** checkbox, same as the main case.
3. Include the widget version number in the PR description (e.g. "v0.4.9").
4. The automation writes to `docs/chat_widget/changelog.md` instead of `docs/changelog.md` — see [Changelog Automation](changelog_automation.md) for how that works.

### 3. Self-hosted operator-impacting changes (manual process)

Check this box when an operator has to *do* something on upgrade — a migration, a new or changed setting, a change in deployment shape, a deprecation or removal, or a security fix that needs operator action such as rotating a credential. See [`RELEASING.md`](https://github.com/dimagi/open-chat-studio/blob/main/RELEASING.md) for how those entries are cut into a tagged release.

1. Check the **"Self-hosted operators must know about or act on this change"** checkbox.
2. Add an entry yourself under `[Unreleased]` in the repo-root `CHANGELOG.md` — this one is not automated.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs
