# Changelog Automation

This covers how the automatic changelog update works internally, and what to do if it doesn't fire for your PR. For what you need to do in your PR to trigger it, see [What to do in your PR](index.md#what-to-do-in-your-pr).

## How the automatic changelog update works

The [dispatch workflow](https://github.com/dimagi/open-chat-studio/blob/main/.github/workflows/docs-changelog-dispatch.yml) runs when a PR targeting `main` that touches files under `apps/`, `components/`, `config/`, `assets/`, or `templates/` is merged with the **"This PR requires docs/changelog update"** box checked in the PR description. It sends a dispatch event to the [docs repo][docs_repo], whose [update-changelog workflow](https://github.com/dimagi/open-chat-studio-docs/blob/main/.github/workflows/update-changelog.yml) gives Claude your PR's title and description. Claude drafts a changelog entry and any documentation updates, and the workflow opens a PR in the docs repo and tries to assign it to you (the assignment is skipped if you don't have access to the docs repo). If Claude makes no changes, no PR is opened.

The automation handles **chat widget** changes (files under `components/`) differently from main app changes:

| Change type | Changelog file | PR base branch |
|---|---|---|
| Main App | `docs/changelog.md` | `main` |
| Widget (`components/`) | `docs/chat_widget/changelog.md` | `widget-develop` |

## If the automation doesn't fire

This can happen when:

- Your PR doesn't touch `apps/`, `components/`, `config/`, `assets/`, or `templates/` (e.g. tech docs-only changes) — the dispatch workflow only triggers on those paths.
- You ticked the checkbox after the PR was merged — the workflow reads the PR description as it was at merge time, so editing it afterwards has no effect.
- You edited the checkbox text. The workflow looks for a ticked box whose text contains "requires docs", "requires changelog" or "requires documentation".
- Your PR came from a fork. GitHub does not give fork PRs access to the secret the dispatch needs, so the dispatch step fails.

If the dispatch was sent but the docs repo's workflow then failed, it posts a comment on your PR with a link to the failed run.

In any of these cases, use one of:

### Manual trigger in docs repo
The [update-changelog workflow](https://github.com/dimagi/open-chat-studio-docs/actions/workflows/update-changelog.yml) in the docs repo can also be triggered manually: go to **GitHub Actions → "Update Changelog and Docs from OCS PR"** and enter the OCS PR number.

### Manual option
Alternatively, you can update the main changelog or widget changelog yourself directly.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs
