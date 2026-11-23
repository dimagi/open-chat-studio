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

### Migrations
<!-- Only migrations that are long-running or locking, irreversible, need a
     manual step or backfill, or change behavior on upgrade. Omit additive
     migrations that run automatically. Omit the section if none qualify. -->
- `experiments.0157` drops the `debug_mode_enabled` column from `experiments_experiment` (#4375).
  Code from before `experiments.0156` still reads that column, so upgrading past both in one
  deploy makes the old release fail its chatbot queries until the new one is serving. Deploy a
  release that contains `0156` first, or stop the old release before migrating. The stored values
  are not kept; reversing the migration re-adds the column with every row `false`.
- `chat.0033` moves the file ids chat messages store under the `cited_files` and `generated_files`
  metadata keys into `ocs_attachment_file_ids`, now the only key read (#1901). It runs one `UPDATE`
  per 10,000 message ids and reads every row of `chat_chatmessage`, so on a large table it takes a
  while; it locks only the rows it changes. Releases before this one write the old keys, so a reply
  the previous release saves after the migration starts keeps them and its files stop showing in the
  transcript. Stop the previous release before migrating. Reversing the migration leaves the data as
  it is.
- `experiments.0158` builds three indexes on `experiments_participant` with
  `CREATE INDEX CONCURRENTLY` (#3796): trigram indexes on `UPPER(identifier)` and `UPPER(name)`,
  and a b-tree on `remote_id`. Writes continue during the build, which can take several minutes
  on a large participant table. The migration is only recorded once all three finish, so if
  a build fails, drop every index it got to, valid or not, before rerunning it:
  `DROP INDEX CONCURRENTLY IF EXISTS` on `participant_identifier_upper_trgm_idx`,
  `participant_name_upper_trgm_idx` and `participant_remote_id_idx`.

### Deprecations and removals
- The legacy web chat (public `/start/` links, invitations, the server-rendered chat page and the
  full-page logged-in chat) is deprecated in favour of the public link channel (#4209). Its
  routes now send RFC 8594 `Deprecation`, `Sunset` and `Link` headers. Once the sunset date in
  `LEGACY_WEB_CHAT_SUNSET_AT` has passed and the new `flag_sunset_legacy_web_chat_enforce` flag
  is on for a team (via `/admin/flags/`, off by default), that team's legacy entry points return
  `410 Gone`. Invitations can no longer be created or sent. A new daily Celery beat task,
  `ocs_notifications.tasks.notify_http_sunsets` (02:30), notifies teams still using the legacy
  chat; Celery beat must be running for those notices to go out.
- The chatbot debug mode setting is removed (#4375). `debug_mode_enabled` is gone from the v2
  chatbot inspect and PATCH responses, and a v2 create or PATCH that sends it gets `400`. The v1
  task poll (`/api/chat/{session_id}/{task_id}/poll/`) returns the generic error message for
  every chatbot; it no longer returns the raw error for chatbots that had debug mode on.
- Message `metadata` in the sessions API no longer has `cited_files` or `generated_files` keys.
  The file ids they held are under `ocs_attachment_file_ids`. The message's `attachments` field
  still lists every attached file. (#1901)

### Configuration
<!-- Every new, renamed, retyped or removed setting that can be configured through
     the process environment, whether or not it is required and whatever its
     default. Also a changed default that alters behavior. State the default and
     whether it is required. -->
- `OUTBOUND_USER_AGENT` (optional, default unset) replaces the User-Agent sent on
  every request made with an auth provider: custom actions, the HTTP pipeline
  node, MCP servers, document sources and OAuth token requests. Left unset, those
  requests send `OpenChatStudio (+<site URL>)`, built from the Site record, where
  they previously sent httpx's `python-httpx/<version>`. A custom action whose
  OpenAPI spec declares a `User-Agent` header parameter sends that value instead.
- `RATE_LIMIT_CUSTOM_ACTIONS` (optional, default `1000/5m`) sets the rate for the
  new `custom_actions` scope, which counts outbound custom action calls per auth
  provider, or per action when it has none. Like the other scopes it only logs
  `rate_limit.would_block` until `RATE_LIMIT_ENFORCE` is on; after that an
  over-limit call is not sent and the LLM receives a tool error.
- `TEAM_DELETION_GRACE_PERIOD_DAYS` (optional, default `30`): how long a team stays disabled
  after an admin deletes it before its data is permanently removed. Deleting a team no longer
  removes its data straight away: the team is disabled at once, and a new daily Celery beat task,
  `teams.tasks.delete_teams_past_grace_period` (03:15), deletes teams whose grace period has passed.
  Celery beat must be running for deletions to complete.

### Deployment
<!-- Changes to the shape of a deployment: process types, Celery queues,
     backing-service versions, resource requirements, new external
     dependencies. -->
- Message moderation runs a `moderate_message` task on the
  `background` queue while the chat task that started it waits up to 30 seconds
  for the result. A worker must be serving the `background` queue. A single
  worker that serves both queues must run more than one task at a time (for
  example `--pool threads`); a single-process worker cannot start the moderation
  task while the chat task waits, so every moderated message is recorded as not
  moderated (`timeout`). (#4704)

### Deprecated
<!-- Signals intent to remove. Every entry names the earliest version in which
     removal may land, and links its deprecation tracking issue. -->

### Removed

### Security
<!-- Also list here anything requiring operator action, e.g. credential
     rotation. -->

---

## [1.0.0-rc1] - 2026-10-20

First tagged release candidate. There is no earlier release to upgrade from, so this
version has no upgrade steps, migration notes or configuration changes to list. Operator
entries start with the next release.

User-facing changes: <https://docs.openchatstudio.com/changelog/>

Bundled chat widget: 0.13.0

Container image: `ghcr.io/dimagi/open-chat-studio:1.0.0-rc1`

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
