# Operator Changelog

Changes that a **self-hosted operator must know about or act on** when upgrading
Open Chat Studio: migrations, configuration, deployment shape, deprecations and
removals, and security fixes.

This is **not** the product changelog. New features, improvements and bug fixes
are described for end users at
<https://docs.openchatstudio.com/changelog/>, and summarised in the
[docs-repo release notes](https://github.com/dimagi/open-chat-studio-docs/releases).
Each version section below links to the user-facing notes covering the same
range, so an operator reads the product story there and the upgrade mechanics
here.

Versioning is [SemVer](https://semver.org/); see `RELEASING.md` for how this
file is maintained and cut. Widget versions are a separate train — see
[widget versioning](docs/developer_guides/widget_versioning.md).

## [Unreleased]

Operator-impacting entries land here as they merge to `main`, and move into a
version section when a release is cut.

### Upgrading
<!-- Manual steps, in the order they must run relative to the deploy. Omit if
     the upgrade is "pull the new tag and deploy". -->

### Migrations
<!-- One line per migration. Omit the section if there are none. -->

### Configuration
<!-- New, renamed, retyped or removed environment variables and settings.
     State the default and whether it is required. -->
- `OCS_VERSION`: new, optional, defaults to `latest`. Read by
  `docker-compose.prod.yml` to select which published image tag to run. Pin it
  to the release you intend to run rather than tracking `latest`. (#4283)

### Deployment
<!-- Changes to the shape of a deployment: process types, Celery queues,
     backing-service versions, resource requirements, new external
     dependencies. -->
- Container images are now published to
  `ghcr.io/dimagi/open-chat-studio` on each release tag, so an upgrade is a
  pull rather than a local build. Images are `linux/amd64` only; arm64
  operators still build from source. The first image is published with the
  first release tag. (#4283)
- The base image moved from Debian 11 (bullseye) to Debian 12 (bookworm),
  because bullseye's LTS window has closed. This affects you only if you build
  the image yourself: ffmpeg goes 4.x to 5.1 and the bundled `psql` client goes
  13 to 15. Audio conversion was verified against ffmpeg 5.1 across the mp3,
  opus and wav paths. No action required if you pull the published image. (#4283)
- Container images no longer contain the `.git` directory. (#4283)

### Deprecated
<!-- Signals intent to remove. Every entry names the earliest version in which
     removal may land, and links its deprecation tracking issue. -->

### Removed
- **Breaking (API):** `/api/v2/.../inspect/` no longer returns an `assistant`
  key on a node, and the `AssistantNodeParams` component is gone from the node
  params union. The key was already conditional — omitted for nodes not
  declaring `assistant_id` — so only clients inspecting assistant-bearing
  pipelines are affected; those nodes still render through the generic node
  shape. `assistant_id` is suppressed rather than falling through to the
  generic params, so no internal id is exposed. (#4357, #4254)
- The OpenAI Assistants UI is gone: `/a/<team>/assistants/` and everything under
  it now 404s, the nav entry is removed, and `assistant_file:` links in
  historical chat messages render as plain text instead of downloads. OpenAI
  retired the Assistants API on 26 August 2026, so the feature had no working
  backend to keep. No migration and no data loss — the `OpenAiAssistant` rows
  and the Django admin for them survive this release; a later phase drops the
  tables. Pipelines holding an assistant node are unaffected by this PR. (#4328,
  #4254)

### Security
<!-- Also list here anything requiring operator action, e.g. credential
     rotation. -->

---

## [X.Y.Z] - YYYY-MM-DD
<!-- Template. Delete any section with no entries; delete this comment block. -->

User-facing changes in this release: link to the docs-repo release notes
covering the same commit range.

Bundled chat widget: `LATEST_VERSION` from `apps/channels/widget_versions.py`.

### Upgrading
- Exact commands and their order relative to the deploy. (#PR)

### Migrations
- `NNNN_migration_name`: reversible yes/no; locking yes/no (expected duration on
  a representative table size); manual steps none/described. (#PR)

### Configuration
- `OCS_EXAMPLE_SETTING`: new, optional, defaults to `x`. (#PR)

### Deployment
- Deployment-shape change and what an operator must do about it. (#PR)

### Deprecated
- Feature X is deprecated; removal no earlier than vN.0.0 and no sooner than
  `YYYY-MM-DD`. Use Y instead. (#issue)

### Removed
- Feature X, deprecated in vA.B.C on `YYYY-MM-DD`. (#PR)

### Security
- Fix description; avoid exploit detail if disclosure timing matters. (#PR)

---

<!-- Filled-out example for reference; delete once real releases exist.

## [1.1.0] - 2026-10-01

User-facing changes in this release:
https://github.com/dimagi/open-chat-studio-docs/releases/tag/...

Bundled chat widget: 0.11.0

### Migrations
- `0142_add_index_filechunkembedding`: reversible, non-locking (uses
  `CONCURRENTLY`), no manual steps. On tables over 5M rows expect 10-15 min
  build time; no maintenance window required. (#812)

### Configuration
- `OCS_EVAL_QUEUE_NAME`: new, optional, defaults to `evaluations`. Only
  relevant if you run per-queue Celery workers. (#799)

### Deployment
- The `evaluations` queue can now be consumed by a dedicated worker. Existing
  single-worker deployments need no change — a worker started without `-Q`
  still consumes every queue. See the
  [hosting overview](docs/hosting/index.md). (#799)

-->
