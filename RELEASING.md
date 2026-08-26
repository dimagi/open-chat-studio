# OCS Release Process

How to cut a tagged release of Open Chat Studio for third-party self-hosted
operators.

This is the internal procedure. The **operator-facing contract** — what a
version number means, cadence, support window, how to upgrade, how to subscribe
— lives in [docs/hosting/releases.md](docs/hosting/releases.md) and is published
at <https://developers.openchatstudio.com/hosting/releases/>. Any change here
that an operator can observe must be reflected there too.

## Scope

This governs the tagged, external-facing release train only. It does not change:

- **Our own deploys.** `deploy.yml` continues to ship every green `main` commit
  straight to prod (dev deploys are a manual `workflow_dispatch`) — see
  [deployment process](docs/developer_guides/deployment.md). Tags are cut from
  commits that have already been through that path; they do not gate it.
- **The user-facing changelog.** Per-PR entries continue to flow to the docs
  repo via the "This PR requires docs/changelog update" checkbox and
  `docs-changelog-dispatch.yml`. See
  [user docs and changelog process](docs/developer_guides/user_docs.md).
- **The weekly docs-repo release notes.** The Monday workflow in the docs repo
  still publishes the user-subscribed release feed, on its own cadence.
- **The chat widget train.** The widget is versioned, published to npm and
  deprecated independently, under `w_v*` tags. See
  [widget versioning](docs/developer_guides/widget_versioning.md). An app
  release records which widget `LATEST_VERSION` it bundles, but does not
  version it.

### How the pieces fit

| Surface | Repo | Audience | Cadence |
|---|---|---|---|
| `docs.openchatstudio.com/changelog/` | docs | product users | per merged PR |
| Docs-repo GitHub Releases | docs | users on the release feed | weekly (Mon) |
| `CHANGELOG.md` + `vX.Y.Z` tags | this | self-host operators | monthly |
| [Chat widget changelog](https://docs.openchatstudio.com/chat_widget/) + `w_v*` tags | docs / this | widget embedders | per widget release |

An operator release note therefore has two halves: **what changed** (link to the
docs-repo notes for the same commit range) and **what you must do about it**
(this repo's `CHANGELOG.md`). Nothing is written twice.

## Choosing the version

The bump rules and their rationale are the operator contract — see
[what a version number means](docs/hosting/releases.md#what-a-version-number-means).
In short: PATCH for fixes, MINOR for backward-compatible change and additive
migrations, MAJOR for anything requiring operator action.

Two calls that need judgement at cut time:

- **Removal of an unused feature is MINOR, not MAJOR.**
  [Feature deprecation](docs/developer_guides/feature_deprecation.md) puts
  features with no active usage on a fast path with no grace period, so treating
  every removal as breaking would force a major bump for routine cleanup and
  destroy the signal MAJOR carries. Ship it as MINOR with a `Removed` entry
  naming the audit. A feature with active usage is the MAJOR case, and only
  after its 60-day lifecycle.
- **The bump follows the highest-impact change in the batch**, not the most
  interesting one. One locking migration makes a release of bug fixes a MAJOR.

`vX.Y.Z` does not collide with the widget's `w_vX.Y.Z` namespace. Do not use
CalVer or build numbers — operators need to reason about compatibility, not
dates.

## Cadence

- **Monthly**, on the first Thursday, cut a release from `main`.
- **Out of cadence** for security fixes and critical regressions affecting
  self-hosters. These are patch releases and ship as soon as the fix is
  verified.

## Cut criteria

Do not tag the tip of `main`. Tag the most recent commit that has been running
in **our** production for at least 24-48 hours with no rollback or hotfix.

Find the candidates from the prod deployment record. `deploy.yml` creates the
deployment record *before* the ECS rollout runs, so a record on its own is not
evidence the deploy succeeded — a deployment needs a `success` status. Check for
`success` anywhere in a deployment's status history, not just its latest status:
statuses are returned newest-first, and every superseded deployment ends up
`inactive,success,pending`, so reading only the newest status hides exactly the
soaked commits you are looking for.

    gh api "repos/dimagi/open-chat-studio/deployments?environment=aws-prod&per_page=10" \
      --jq '.[] | "\(.id) \(.created_at) \(.sha)"' |
    while read -r id created sha; do
      states=$(gh api "repos/dimagi/open-chat-studio/deployments/$id/statuses" \
        --jq '[.[].state] | join(",")')
      case ",$states," in *,success,*) echo "$created ${sha:0:9}  [$states]" ;; esac
    done

`inactive` on an older deployment is normal — it means a later deploy superseded
it, not that anything went wrong. A deployment with no `success` in its history
failed or is still in flight; skip it.

If a rollback or hotfix occurred in the soak window, move to the fixed commit and
restart its soak clock.

## Release steps

1. Identify the candidate commit per "Cut criteria".
2. Move `CHANGELOG.md` `[Unreleased]` entries into a new version section. Verify
   them against the actual diff since the last tag — in particular, that every
   migration in the range has a line, and every changed setting is listed:

       git log --oneline <last-tag>..<sha>
       git diff --stat <last-tag>..<sha> -- '*/migrations/*' 'config/settings*.py' .env.example

3. Record the bundled widget version (`LATEST_VERSION` in
   `apps/channels/widget_versions.py`).
4. Link the docs-repo release notes covering the same commit range.
5. Confirm the bump matches the highest-impact change in the batch (see
   "Choosing the version").
6. If anything in the range is breaking, confirm it completed the
   [feature deprecation](docs/developer_guides/feature_deprecation.md)
   lifecycle. If not, hold it and ship the deprecation notice instead.
7. Tag and push: `git tag vX.Y.Z <sha> && git push origin vX.Y.Z`.
8. Publish a GitHub Release in **this** repo with the changelog section as the
   body. Title it `vX.Y.Z` so it is not confused with the docs-repo release
   feed.
9. Post to
   [Discussions → Announcements](https://github.com/dimagi/open-chat-studio/discussions/categories/announcements)
   and link it from the Release body — see
   [Announcing a release](#announcing-a-release).

## Writing migration notes

For each Django migration in the release, state:

- **Reversible?** Yes/No.
- **Long-running or locking?** If yes, expected duration on a representative
  table size, and whether a maintenance window is advised.
- **Manual steps?** Exact commands and their order relative to the deploy.
- **Data backfill?** Automatic post-deploy, or a manual management command.

If there are none, say "No migrations in this release."

Write these for someone with a database you cannot see. "Fast on our data" is
not a note; a row count and a duration is.

## Breaking changes

Removals and breaking changes follow
[feature deprecation](docs/developer_guides/feature_deprecation.md), which is
the authority on audit, comms and timing — including the **60-day minimum**
between announcement and removal for features with active usage. This process
adds only the release-train mapping:

- A breaking change appears as **Deprecated** in a release before it appears as
  **Removed**, and its Deprecated entry names the earliest version in which
  removal may land.
- With a monthly cadence, the 60-day window spans at least two releases — one
  release cycle is not sufficient notice.
- Major releases carry an "Upgrading to vX.0.0" section summarising every
  breaking change and the required action.

## Announcing a release

Announcements go to **GitHub Discussions** in this repo, in the
[Announcements](https://github.com/dimagi/open-chat-studio/discussions/categories/announcements)
category — the channel operators are told to watch in
[releases.md](docs/hosting/releases.md#staying-informed).

- **Every release** gets a post: the version, a one-line summary, and links to
  the GitHub Release and the docs-repo notes for the same range. Keep it short —
  the release body is the detail.
- **Security releases and breaking changes** get their own post, titled so the
  severity is legible in a notification email (e.g. `Security release v1.2.1 —
  action required`). Lead with what an operator must do, not with what changed.
- **Deprecations** are announced when the notice ships, not when the removal
  does, naming the earliest version removal may land in. This is the same
  announcement the
  [feature deprecation](docs/developer_guides/feature_deprecation.md) comms
  levers call for, aimed at self-hosters.

Post from the release manager account via **New discussion** in the
Announcements category, and link the discussion from the GitHub Release body so
the two are navigable in both directions. When this gets automated (see
[Known gaps](#known-gaps)), it is the GraphQL `createDiscussion` mutation
against category `Announcements` — there is no supported REST create endpoint.

## Known gaps

Deliberately unresolved at the time this process was written:

1. **No published container image.** Operators clone and build from the tag. A
   workflow publishing `ghcr.io/dimagi/open-chat-studio:vX.Y.Z` on tag push
   would make upgrades a pull instead of a build, and commits us to maintaining
   a public image.
2. **No version surfaced in the app.** `pyproject.toml` says `0.1.0` and is
   unused. An operator cannot tell what they are running. Needs a single source
   of truth, bumped at tag time and exposed (footer, health endpoint, or both).
3. **Announcement posts are manual.** Step 9 is a hand-written Discussions post.
   Worth automating off the tag push once the cadence has settled.

## Ownership

Release manager: **[define rotation]**. The release manager for a cycle owns
steps 1-9.
