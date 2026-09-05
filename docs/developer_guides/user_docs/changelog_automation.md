# Changelog Automation

This covers how the automatic changelog update works internally, and what to do if it doesn't fire for your PR. For what you need to do in your PR to trigger it, see [What to do in your PR](index.md#what-to-do-in-your-pr).

## How the automatic changelog update works

The [dispatch workflow](https://github.com/dimagi/open-chat-studio/blob/main/.github/workflows/docs-changelog-dispatch.yml) runs when a PR targeting `main` that touches files under `apps/`, `components/`, `config/`, `assets/`, or `templates/` is merged with the PR description box checked. It sends a dispatch event to the [docs repo][docs_repo], which then uses Claude AI to analyze the changes and open a PR with documentation updates and a changelog entry on your behalf.

The automation handles **chat widget** changes (files under `components/`) differently from main app changes:

| Change type | Changelog file | PR base branch |
|---|---|---|
| Main App | `docs/changelog.md` | `main` |
| Widget (`components/`) | `docs/chat_widget/changelog.md` | `widget-develop` |

## If the automation doesn't fire

This can happen when your PR doesn't touch `apps/`, `components/`, `config/`, `assets/`, or `templates/` (e.g. tech docs-only changes) — the dispatch workflow only triggers on those paths. In that case, use one of:

### Manual trigger in docs repo
The [update-changelog workflow](https://github.com/dimagi/open-chat-studio-docs/actions/workflows/update-changelog.yml) in the docs repo can also be triggered manually: go to **GitHub Actions → "Update Changelog and Docs from OCS PR"** and enter the OCS PR number.

### Manual option
Alternatively, you can update the main changelog or widget changelog yourself directly.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs
