# Operator Changelog

Changes that a **self-hosted operator must know about or act on** when upgrading
Open Chat Studio: manual steps, migrations that need attention, configuration
operators must set or that changes behavior, deployment shape, deprecations and
removals, and security fixes.

Add an entry only if an operator would have to do something, or be surprised,
because of the change. Every new, renamed, retyped or removed environment-configured
setting belongs here. Additive migrations that run automatically do not. See
[what belongs in the changelog](RELEASING.md#what-belongs-in-changelogmd).

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
- **After** deploying, run `python manage.py retire_assistant_file_purpose`. It re-points files
  the retired OpenAI Assistants feature left attached to a collection or a chat message onto
  their real purpose, and deletes the ones nothing references any more (reclaiming their
  storage). Archived files are left untouched. `--dry-run` reports what it would do. Safe to run
  later; until it runs, those rows keep a `purpose` value that is no longer a valid choice, which
  nothing reads. (#4254)

### Migrations
<!-- Only migrations that are long-running or locking, irreversible, need a
     manual step or backfill, or change behavior on upgrade. Omit additive
     migrations that run automatically. Omit the section if none qualify. -->
- Every OpenAI Assistant is deleted, along with its tool resources and any custom action
  operation attached to one. `Node.assistant` is nulled and the mirrored `assistant_id` is
  stripped from stored pipeline node params. Any `Banner` pinned to the removed
  `assistants_home` location is deleted; it had nowhere left to render. This is deliberate,
  irreversible data loss for a feature whose upstream API OpenAI retired on 26 August 2026;
  the audit log retains the history.
  The `assistants` tables and the two FK columns still exist and are dropped in the following
  release. (#4254)

### Deprecations and removals
- The legacy web chat (public `/start/` links, invitations, the server-rendered chat page and the
  full-page logged-in chat) is deprecated in favour of the public link channel (#4209). Its
  routes now send RFC 8594 `Deprecation`, `Sunset` and `Link` headers. Once the sunset date in
  `LEGACY_WEB_CHAT_SUNSET_AT` has passed and the new `flag_sunset_legacy_web_chat_enforce` flag
  is on for a team (via `/admin/flags/`, off by default), that team's legacy entry points return
  `410 Gone`. Invitations can no longer be created or sent. A new daily Celery beat task,
  `ocs_notifications.tasks.notify_http_sunsets` (02:30), notifies teams still using the legacy
  chat; Celery beat must be running for those notices to go out.
- The `flag_public_channel` feature flag is removed. The public link channel is offered to every
  team; a deployment that kept the flag off now shows it in the channel dropdown. Public links
  are served on the Django `Site` domain, so check it matches the deployed host (see
  [Public link host](docs/hosting/configuration.md#public-link-host)). The orphaned flag row
  appears under Legacy on the Feature Flags page at `/admin/flags/` and can be deleted there.
  (#4793)
- The OpenAI Assistants feature is fully removed (#4254). The `Assistant Admin` role is gone —
  members holding it lose add/change/delete on files, which no other role grants; re-grant file
  access through another role if a team relied on it. `Attachment.upload_to_assistant` no longer
  exists, so a Python node that assigns it will now fail at runtime. `FilePurpose.ASSISTANT` and
  the `assistants_home` banner location are removed, as is the `assistant` field from the
  `CustomActionOperation` and `Node` export-API schemas (it was always `null` in exports) and the
  `assistant` value from `PurposeEnum`.

### Configuration
<!-- Every new, renamed, retyped or removed setting that can be configured through
     the process environment, whether or not it is required and whatever its
     default. Also a changed default that alters behavior. State the default and
     whether it is required. -->
- `SENTRY_BROWSER_LOADER_KEY`: new, optional, defaults to empty (no browser-side
  Sentry). Previously every non-debug deployment loaded the Open Chat Studio
  project's Sentry browser script and reported browser errors and traces to it.
  Set it to the key of your own Sentry Loader Script to report to your project.
- `SENTRY_TRACES_SAMPLE_RATE`: new, optional, defaults to `0` (no tracing).
  Fraction of requests and Celery tasks sent to Sentry as performance traces;
  only has an effect when `SENTRY_DSN` is set. Sentry no longer adds
  `sentry-trace`/`baggage` headers to outbound HTTP requests.
- `OCS_VERSION`: new, optional, defaults to `latest`. Read by
  `docker-compose.prod.yml` to select which published image tag to run. Pin it
  to the release you intend to run rather than tracking `latest`. (#4283)
- `OCS_LANGFUSE_PUBLIC_KEY`, `OCS_LANGFUSE_SECRET_KEY`, `OCS_LANGFUSE_HOST`,
  `OCS_LANGFUSE_SAMPLE_RATE`: new, optional. Trace the System Agent's LLM calls
  (code generation, filter building) in Langfuse. Unset by default (no
  tracing); this is separate from a team's own Trace Provider. (#4395)
- `DATA_UPLOAD_MAX_MEMORY_SIZE`: new, optional, defaults to `10485760` (10 MB).
  Caps the request body Django buffers in memory. See the
  [configuration reference](docs/hosting/configuration.md#security). (#4411)
- `PRELOGIN_CONTACT_EMAIL`, `HUBSPOT_FORM_REGION`, `HUBSPOT_FORM_PORTAL_ID`,
  `HUBSPOT_FORM_ID`, `PRELOGIN_DEMO_BOTS`: removed. The pre-login marketing
  pages that read them are gone (see Deployment below). Setting them is now a
  no-op rather than an error, so no action is required, but they can be dropped
  from your environment. (#4389)
- `OAUTH_ACCESS_TOKEN_EXPIRE_SECONDS`, `OAUTH_CHAT_START_TOKEN_EXPIRE_SECONDS`:
  new, optional, default `36000` and `60`. Lifetime in seconds of OAuth access
  tokens issued at `/o/token/`; the second applies to client-credentials tokens
  requested with the `chat:start` scope alone, which are meant for the chat
  widget. Previously every token lived 36000 seconds. (#4472)

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
- The app no longer serves the marketing pages. `/` renders a small landing page
  for anonymous visitors, and `/about/`, `/applications/`, `/contact/`,
  `/open-opportunities/` and `/platform/` now `301` to
  `PROJECT_METADATA["MARKETING_SITE_URL"]`, which defaults to Dimagi's marketing
  site. A fork that wants those paths to point somewhere else — or to serve its
  own pages there — should override that key in `config/settings.py`. (#4389)

### Deprecated
<!-- Signals intent to remove. Every entry names the earliest version in which
     removal may land, and links its deprecation tracking issue. -->

### Removed
- OpenAI Assistants are fully removed. A pipeline holding an assistant node
  still opens in the editor — the node renders as a "Removed Node" — but the
  pipeline no longer builds, so it cannot run. OpenAI retired the Assistants API
  on 26 August 2026, so these pipelines were already failing at the provider.
  The `OpenAiAssistant` rows are deleted by this release (see Migrations above)
  and the Django admin for them is gone; the `assistants` tables and the FK
  columns are dropped in the following release. (#4372, #4254)
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
  backend to keep. (#4328, #4254)

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
