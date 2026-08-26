# Releases, Versions and Upgrades

What Open Chat Studio's version numbers mean, how often releases happen, how
long each one is supported, and how to hear about them. This is the contract
for self-hosted operators.

If you are cutting a release rather than consuming one, see
[RELEASING.md](https://github.com/dimagi/open-chat-studio/blob/main/RELEASING.md)
in the repository root.

## What a version number means

Releases are tagged `vMAJOR.MINOR.PATCH` and follow
[SemVer](https://semver.org/). The first release is `v1.0.0`.

| Bump | What it means for you |
|------|----------------------|
| PATCH | Bug and security fixes. Nothing to act on: no schema, config or behaviour change. |
| MINOR | New features and backward-compatible changes. May include additive migrations, which run automatically with no manual steps. |
| MAJOR | Requires action. Manual migration steps, removed or renamed configuration, breaking API or webhook changes, or the removal of a feature that was in use. |

Two things worth knowing:

- **A MINOR release can remove a feature** — but only one that a usage audit
  found nobody was using. Anything with real usage goes through the full
  [deprecation lifecycle](#deprecations) and its removal is a MAJOR release.
- **The chat widget is versioned separately** under `w_vX.Y.Z` tags, with its
  own [changelog](https://docs.openchatstudio.com/chat_widget/) and deprecation
  schedule. Each app release records which widget version it bundles, but
  upgrading the app does not change the widget your sites embed — that is pinned
  in your embed snippet.

## Cadence

- A release is cut **monthly**, on the first Thursday.
- **Security fixes and critical regressions** ship out of cadence, as patch
  releases, as soon as they are verified.

Releases are never cut from the tip of `main`. Every tag points at a commit that
has already run in Dimagi's own production for at least 24-48 hours without a
rollback.

## What changed in a release

Each release has two sets of notes, for two different questions:

| Question | Where |
|---|---|
| What's new, improved or fixed? | [Product changelog](https://docs.openchatstudio.com/changelog/) |
| What do I have to *do* about it? | [`CHANGELOG.md`](https://github.com/dimagi/open-chat-studio/blob/main/CHANGELOG.md) in the repository |

`CHANGELOG.md` is the operator-facing one: migrations, configuration changes,
changes to the shape of a deployment, deprecations, removals, and security
fixes. Read that one before upgrading. Each version section links the product
notes covering the same range, so start there and follow the link for the
feature story.

The [GitHub Release](https://github.com/dimagi/open-chat-studio/releases) for a
tag carries the same operator notes as its body.

## Getting a release

Each release is published as a container image to
[`ghcr.io/dimagi/open-chat-studio`](https://github.com/dimagi/open-chat-studio/pkgs/container/open-chat-studio),
tagged `1.2.3`, `1.2`, and `latest`. Set `OCS_VERSION` to the release you want
and pull it:

```bash
# .env
OCS_VERSION=1.2.3
```

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

**Pin `OCS_VERSION` to an exact release** rather than tracking `latest`. It
makes an upgrade a change you chose, keeps the previous image on disk to roll
back to, and means `docker compose up` on a rebuilt host doesn't silently jump
versions.

Images are `linux/amd64`. On arm64, build from source instead.

### Building from source

Still supported, and required on arm64. Check out the tag first — see
[Docker Compose deployment](docker.md). Do not build from `main`: it is Dimagi's
continuous-deployment branch, moves several times a day, has not been through
the release soak, and carries no migration notes.

## Upgrading

1. Read the `CHANGELOG.md` sections for **every** version between yours and the
   target — not just the target's.
2. Check the **Migrations** notes for anything long-running or locking, and
   whether a maintenance window is advised.
3. Check the **Configuration** notes and update your environment before
   deploying.
4. For a MAJOR release, work through its "Upgrading to vX.0.0" section, which
   lists every breaking change and the action each requires.
5. Back up your database. Some migrations are not reversible, and the notes say
   which.
6. Bump `OCS_VERSION`, then `docker compose -f docker-compose.prod.yml pull`
   and `up -d`. The `migrate` service runs migrations before `web` starts.

**Upgrade one minor at a time.** Upgrading from the immediately preceding minor
release is what gets tested. Skipping versions is unsupported — if you are
several minors behind, upgrade through each intermediate minor rather than
jumping straight to the newest.

## Support window

The current minor release (N) and the one before it (N-1) receive patch and
security fixes. Because a monthly release may be a patch rather than a minor,
that is *at least* two months of cover.

Older minors do not receive fixes, including security fixes, and we do not
backport further back. If you are behind, upgrading is the fix.

## Deprecations

A `Deprecated` entry in `CHANGELOG.md` is a warning with a deadline. It names
the earliest version in which the feature may be removed, and links a tracking
issue where you can object or ask for a migration path.

For anything with active usage, removal is at least **60 days** after the
deprecation is announced, which spans at least two releases. Removal itself
appears as a `Removed` entry in a later release. If a deprecation affects you,
the tracking issue is the place to say so — that window exists to be used.

## Staying informed

Release announcements go to **GitHub Discussions** in the
[Announcements](https://github.com/dimagi/open-chat-studio/discussions/categories/announcements)
category. Watching that category is the supported way to hear about a release.

To subscribe: open the
[Announcements category](https://github.com/dimagi/open-chat-studio/discussions/categories/announcements)
and use **Watch** (or watch the repository and enable Discussions
notifications). Every release gets a post; security releases and breaking
changes get their own, titled so the urgency is visible in a notification email.

Running a self-hosted deployment without watching Announcements means you will
not hear about security releases.
