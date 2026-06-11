---
status: active
---

# Team Data Sync — Overview

## Glossary

- **Source Server**: The server being migrated from.
- **Target Server**: The server being migrated to.

## Overview

The migration process runs as a management command on the target server. This command fetches a
team's data from the source server over HTTPS and recreates it locally using the ORM.

It is **manifest-driven**: the command hardcodes no model order. It fetches a manifest that lists
every content type to pull, in dependency order, with the per-type config it needs; then for each
entry it pages through a generic content-type endpoint and, for every row, resolves the row's foreign
keys, creates the row locally, and records an FK translation. That single loop — **pull, translate
FKs, create, record** — is the whole sync. Each invocation makes a **single pass** and then exits; to
re-sync the live data, the operator simply reruns the command (see *The sync process*).

Two source-side read endpoints, both new and served under the `/api/v2/sync/` prefix, plus the
existing Files API:

- **Manifest** — `GET /api/v2/sync/manifest/`: the ordered list of content types the command must
  pull, each with the per-type config the caller needs (`phase`, `cursor`, `secret`, `order_by`,
  `through`). The manifest is both the **call order** and the **allowlist** — the command hardcodes no
  order, and the slug endpoint refuses any content type not in the manifest.
- **Content-type slug** — `GET /api/v2/sync/<app_label.model>/?cursor=<keyset>&limit=<n>[&public_key=<base64>]`:
  returns one model's team-scoped rows, paginated. Each response holds **one model's rows only** —
  nothing nested or bundled.
- **[Existing] Files API**: Fetches a file's content (bytes) given its metadata.

## Setup

Running the command creates a local SQLite DB named for the team slug and initialises the
`FKTranslation` table. No chatbots, users, service providers, or other resources may be created on
the **source** while the migration runs. Before the first call, the command fetches the manifest and
generates one ephemeral RSA keypair for the run.

**FK translation rule.** Every source row we sync gets an `FKTranslation` row keyed by
`(content_type, source_key)`, with `target_key` left null until the row exists on the target. Every
foreign-key field in every response is resolved through this table; creating a row fills in its
`target_key`. The table doubles as the checkpoint (see *Parity check*) and lets the command be re-run
to resume.

**Timestamp preservation rule.** Every record returned by the slug endpoint carries the source row's
`created_at` and `updated_at` values (for every model with those fields — i.e. anything extending
`BaseModel`, which defines them as `auto_now_add`/`auto_now`). Because those Django field options
ignore any value supplied on insert/save, the ORM cannot set them directly. So immediately after
creating (or upserting) each row, the command issues a raw SQL `UPDATE` to write the source
`created_at`/`updated_at` back onto the new row. This applies to every resource and keeps the target's
timestamps faithful to the source. Example fields are shown on a couple of rows in the responses
below; they are present on **all** records, omitted elsewhere for brevity.

## The sync process

After fetching the manifest and generating the run's RSA keypair, the command walks the manifest
entries in order. For each entry it pages through `GET /api/v2/sync/<slug>/` (passing the run's
`public_key` when the entry is `secret: true`), and for every row it:

1. **Resolves foreign keys** — each FK field value is a *source* pk; the command looks it up in
   `FKTranslation` to get the target pk (FK handling detailed below).
2. **Creates or upserts** the row locally — upsert-by-source-pk, so re-runs and re-served boundary
   rows are idempotent — and records the `(content_type, source_pk) → target_pk` mapping in
   `FKTranslation`.
3. **Writes the timestamps back** — a raw SQL `UPDATE` restores the source `created_at`/`updated_at`
   (timestamp rule). `analysis.analysisquery` is the lone exception: a plain `Model` with no timestamp
   fields, so it skips this step.

The manifest order **is** the dependency order: a referencing model's slug always follows the slug of
whatever it points at, so every FK resolves against an already-created row. Because `FKTranslation`
doubles as the checkpoint (a null `target_key` means "not yet created"), the command can be re-run at
any time to resume where it left off.

Each manifest entry carries a `phase`:

- **`structural`** — synced once. The team's stable building blocks and configuration: the team,
  users, memberships, service providers and their models/voices, custom actions, source materials,
  consent forms, surveys, tags, collections, document sources, notification config, pipelines, nodes,
  chatbots, channels, and events.
- **`live`** — synced as a delta on each run. The tables that grow with chatbot interactions:
  participants, sessions, chats, messages, traces, pipeline chat history, scheduled messages,
  notifications, evaluations, human annotations, transcript analysis, annotations, and assessment
  scores. These rows keep changing on the source while the migration is underway, so a run pulls
  whatever is new or changed since the previous run.
- The single `files.file` entry is **`structural+live`** — synced in both passes (collection-backed
  files in the structural pass, chat-attachment-backed files in the live pass).

**The command runs as a single pass — it does not loop.** One run pulls all structural entries to
build the team, then syncs every live entry once, as a delta, and exits. Structural is idempotent, so
on a rerun its already-created rows are skipped via the checkpoint and that pass is a quick no-op; the
live entries are then re-synced. To take another iteration of the live data, the operator simply
**reruns the command** — there is no internal polling loop and no internal scheduler. Each live slug's
cursor is persisted in the local DB, so every rerun resumes that slug's delta exactly where the
previous run ended; it never re-pulls from the start. The operator reruns as often as they want during
the migration; the final rerun happens after cutover freezes the source (see *Cutover*).

### Foreign keys and many-to-many

Every row arrives carrying primary keys (ids) from the **source** server. Those numbers mean nothing
on the target: the target's rows were created fresh and were assigned their own ids. So before the
command can create a row, it must rewrite every reference inside that row from the source id to the
matching target id — which it finds by looking the source id up in `FKTranslation`. The hard part is
not the rewrite; it is *finding every reference*, because references show up in three different forms.

**1. Normal foreign-key columns** — for example `Experiment.consent_form`, an ordinary database column
holding the id of a related row.

The command does not keep a hand-written list of "which fields on which models are foreign keys" —
such a list would fall out of date the moment someone adds a field. Instead it inspects each model at
runtime: Django can report every field on a model (via `model._meta.get_fields()`) along with each
field's type. For every field that is a foreign key, the command:

  1. reads the source id stored in that column,
  2. determines which model the column points at, and
  3. looks up `(that model, source id)` in `FKTranslation` to get the target id, and writes that into
     the new row.

Because the field list is read from the model itself, a newly added foreign key is translated
automatically, with no code change. The one deliberate gap: a foreign key that points at a model this
sync does **not** copy — an assistant or an MCP server — has no `FKTranslation` entry, so it is left
pointing at nothing. That broken reference is expected and is dealt with separately, outside this sync.

**2. Foreign keys hidden inside JSON fields.** Some references are not stored as ordinary database
columns at all — the id sits inside a JSON value on the row, where the automatic field-by-field scan
described above can never find it. The command handles these with a small amount of purpose-built
code, chosen by the kind of row it has just fetched: when it pulls a pipeline or a pipeline node, it
knows that row carries references inside its JSON and runs the matching handler instead of relying on
the automatic scan.

There are two kinds of hidden reference, and they differ in one important way: whether the JSON itself
says which model each id points at.

**Pipeline node settings.** A pipeline node keeps its settings in a JSON field called `params`, and a
pipeline keeps a copy of all its nodes' settings inside its graph (the `data` field). These settings
contain ids — the id of an LLM provider, a source material, a collection, and so on — but they never
record which model an id belongs to. That is decided by the name of the field the id sits under: an id
under `llm_provider_id` is always an LLM provider, an id under `source_material_id` is always a source
material. Custom actions are the one special case — they are written as `"action_id:operation_id"`
strings rather than plain ids.

The inspect code already knows how to read these settings, and the sync reuses the parts that find the
references (all in `apps/api/v2/inspect/`):

- `RESOURCE_PARAM_FIELDS` lists every settings field that holds a reference and, for each, names the
  kind of resource it points at.
- `ResourceKind.iter_raw_ids(value)` returns the ids inside a field's value, whether that value is a
  single id, a list of ids, or the `action_id:operation_id` form.
- `iter_resource_refs(node_type, params)` combines the two: for a given node it returns each reference
  as a `(kind, id)` pair, skipping fields the node does not use.

What this code does not give us is the last step — from a kind to the model it actually points at. That
link is not written down anywhere reusable; it lives only inside the team-scoped resource loader in
`apps/api/v2/inspect/resources.py`. So the sync keeps its own short table mapping each kind to its
model, which is what `FKTranslation` is keyed on. With that table in hand, the handler walks every
reference in a node's settings: it reads the ids, looks each one up in `FKTranslation` under the kind's
model to get the matching target id, and writes the target ids back into the settings — keeping the
`action_id:operation_id` shape where it applies.

Two things follow. Because the list of reference fields is shared with the inspect code, a newly added
settings field is handled with no extra work; but a newly added *kind* of resource must be added to the
sync's kind-to-model table at the same time. And a reference to a resource the sync deliberately skips,
such as an assistant, has no entry in `FKTranslation`, so it is left unresolved — exactly as in case 1.

**Generic foreign keys.** A few models can point at *any* model rather than one fixed one — a tag
attached to something, a comment on something, a score on something. (Django calls this a generic
foreign key.) Such a reference is stored as two columns: one naming the model being pointed at, and one
holding that row's id. Both are sent in the response, and the model is sent as a readable
`"app_label.model"` string such as `"chat.chatmessage"` rather than its database id, which differs from
one server to the next.

Here the row already names its own model, so the handler needs no lookup table — it fills in the new
record's two columns directly:

1. **Find the target model.** Match the `"app_label.model"` string to the same model on the target.
   This is a plain match by name, because these model entries are a built-in part of Django and are
   identical on both servers. The result becomes the new row's "which model" column.
2. **Translate the id.** Using that model together with the source id, look up the matching target id
   in `FKTranslation`, and write it into the new row's "which row" column.

Once both columns are set, the new record points at the correct row on the target.

**3. Many-to-many relationships** — for example a `ChatAttachment` linked to several `File` rows.

A many-to-many link cannot be set while the row is being created, because both sides must already
exist. So the command always creates the row first and attaches its many-to-many links afterwards
(with Django's `.set()`). How a link travels depends on whether the join between the two rows carries
any extra data of its own:

  - **Plain links (no extra columns).** Most many-to-many links are simply "row A is connected to rows
    X, Y, Z." Django stores these in an automatically created join table that holds nothing but the two
    ids. The serializer emits the link as a plain list of target ids on the owning row — for example a
    `chat.chatattachment` row carries `"files": [501, 502]`. The command translates that list of ids
    and calls `.set()` once the row exists. The join table is never requested on its own.
  - **Links that carry their own data** — for example `documents.collectionfile`, the join between a
    `Collection` and a `File`. Here the join row also stores fields of its own (`status`,
    `external_id`, `metadata`). A plain id list would discard that data, so this kind of join model is
    pulled as its own slug (marked `through: true` in the manifest) and created like any other row, so
    its extra columns are preserved.

### Versions (working before published)

`experiments.experiment` and `pipelines.pipeline` are pulled `order_by working_version_id NULLS
FIRST, id`. Working versions have a null `working_version`, so every working version comes before any
published one across the whole stream — the working version always exists before a published version
references it via `working_version`. No separate version manifest is needed.

### Secrets

Entries flagged `secret: true` are called with `?public_key=<base64>`; the source seals the model's
secret fields under the run's public key and the command unseals them on insert. This covers the
provider configs, `documents.documentsource.config`, `bot_channels.experimentchannel.extra_data`, and
`experiments.participantdata.data` / `encryption_key`. See the [Secrets](#secrets) section for the
sealing mechanism.

## Files and content hydration

File *metadata* travels through the `files.file` slug like any other model; file *content* is
transferred separately, and chunk embeddings come over their own slug.

- **One `files` slug, both phases.** `files.file` is pulled in the structural phase (collection-backed
  files) and re-polled in the live phase (chat-attachment-backed files), so there is no separate
  attachment-files path. A `chat.chatattachment` row carries its backing files as a remapped `files`
  pk list (bare M2M); the importer applies it with `.set()` once the backing `File` rows have arrived
  via the live re-poll.
- **Content option A (per-file API fetch).** For each `File` row, fetch the content bytes from the
  source via the existing Files API and store them against the created `File` record. The target
  assigns its own storage key.
- **Content option B (bulk zip).** An admin dashboard action on the source lets the user download all
  of the team's files as one zip and upload it once to the target's storage bucket. Django stores only
  the storage key on each `File` row, not the bytes, so the records resolve to the uploaded objects —
  provided the export preserves each file's original storage key and the upload keeps the same layout.
  No per-file API calls.
- **Embeddings.** `files.filechunkembedding` carries each chunk's text plus its vector, paginated by
  primary key. Transferring embeddings as data lets the target skip re-running the embedding model.
  Each row references `file` and `collection`, so the slug is ordered after `files.file` and
  `documents.collection`.

```json
GET /api/v2/sync/files.filechunkembedding/?cursor=0
{
  "next_cursor": 5000,
  "has_more": true,
  "results": [
    { "id": 100, "file": 11, "collection": 10,
      "chunk_number": 0, "page_number": 1,
      "text": "Our refund policy ...",
      "embedding": [0.0123, -0.0456, "... (one float per dimension)"],
      "created_at": "2024-02-01T10:00:00Z", "updated_at": "2024-02-01T10:00:00Z" }
  ]
}
```

## Scheduled-message firing gate

`poll_scheduled_messages` is a global, unconditional beat task, so a synced `ScheduledMessage` with a
due `next_trigger_date` would fire on the target **while the source is still live**. The target
therefore holds a **per-team "migrating" gate** that excludes teams under migration from
`get_messages_to_fire()` (and timeout-trigger firing), so synced `ScheduledMessage` rows sit inert
until cutover clears the gate. The persisted live-data cursors capture the source's final firing
state, so a later rerun never repeats an already-sent occurrence. `events.timeouttrigger` rows keep
their source `config_changed_at` so the trigger's retroactive-firing gate
(`TimeoutTrigger.timed_out_sessions()` filters messages `>= config_changed_at`) carries the source's
semantics rather than resetting to import time. Cutover (below) defines the full
suspend → flip → final-run → clear-gate sequence.

## Parity check

When the migration is complete, no `FKTranslation` row may have a null `target_key` — every synced
source row must have a created target row. A null entry means a resource was missed or a dependency
was never created; the command can be re-run to fill the gaps.

**File content.** Under content option A, the FK-table check covers files implicitly — each row's
bytes were fetched and stored as it was created. Under option B the bytes arrive out of band, so the
check instead spot-checks a sample of files (e.g. 20–50 across the team) and confirms each has content
at its expected storage key. Loading every file's bytes is unnecessary; a sample confirms the upload
landed.

## Cutover

Cutover is the single, team-wide switch from the source to the target. Until this point the source is
still live and the target — though fully built and kept current by repeated reruns of the command — is
held **inert** by the per-team firing gate. Cutover flips inbound traffic and outbound firing over to the
target in one controlled window. It is **all-at-once for the team**, not bot-by-bot: channels that
share a provider physically move together (below), so the team is the clean unit and the firing gate
stays **per-team**.

### Webhook re-registration by platform

Each channel's inbound webhook embeds the server's domain, so cutover re-points each platform at the
target. The *unit* of that flip differs by platform — which is why some are auto-flipped, some are
reported as provider-level, and some as per-channel:

| Platform | Inbound keyed by | Flip unit | Cutover |
| --- | --- | --- | --- |
| Telegram | per-bot token (`setWebhook`) | per channel | **auto** |
| WhatsApp/FB via Twilio | per phone number (Twilio API) | per channel | **auto** |
| WhatsApp via Turn.io | per number/account | per channel | manual |
| Web / Embedded Widget | the embed/widget's target domain | per embed | manual (customer-side; no webhook) |
| API | client's base URL | per client | manual (client-side; no webhook) |
| WhatsApp via Meta Cloud | the Meta **app** callback URL | per provider/app | manual, coupled |
| Slack | the **workspace/installation** | per workspace | manual, coupled |
| SureAdhere | the **tenant** (`tenant_id`) | per tenant | manual, coupled |
| Email | one Anymail inbound webhook (mail-provider/domain) | instance-level | ops only |
| CommCare Connect | the Connect server's shared endpoint | instance/app-level | ops only |
| Evaluations | internal only | — | n/a |

The dividing line is whether inbound is keyed by a **per-bot credential** (Telegram token, Twilio
number — individually flippable, so the command automates them) or by **one shared account/app/domain**
(Meta/Slack/SureAdhere — flipping it moves *every* bot on that provider at once). That shared case is
what the table marks **coupled**: the bots sharing a Meta app / Slack workspace / SureAdhere tenant
all ride a single inbound registration, so they cannot be moved one at a time — flipping that one
registration switches all of them to the target at the same instant, so every bot on it must cut over
together. Email and CommCare Connect are configured once at the **deployment level** and shared across
teams, so a single-team migration cannot flip them in isolation — they are an out-of-band ops task the
command only reports.

### Cutover sequence

1. **Suspend source firing (team).** Exclude the team from the source's `poll_scheduled_messages`
   (and timeout-trigger firing). Scheduled messages fire on a timer independent of inbound traffic, so
   freezing the source's outbound timer *first* is what prevents the same message being sent from both
   servers. Inbound chat still works on the source at this point.
2. **Flip the webhooks.** The command auto-flips Telegram and Twilio immediately, then prints two
   lists for the operator: **providers to update** (each Meta app / Slack workspace / SureAdhere
   tenant — flipping one moves all its bots) and **channels to update** (Turn.io, Web/widget embeds,
   API base-URL). Inbound moves to the target per channel as each flip lands. The target serves those
   conversations immediately — the firing gate governs only *scheduled-message* firing, not inbound
   handling.
3. **Keep re-running through the window.** Until the last webhook is flipped, messages to
   not-yet-flipped channels still land on the source, so keep re-running the command to pull the new
   live data. The window in which some channels point at the target and others at the source is
   data-safe: source firing is frozen and each rerun pulls the stragglers.
4. **Final run once the source is quiet.** After the last channel is flipped (no new data can reach
   the source), run the command one last time so the target is fully caught up, including the frozen
   scheduled-message state.
5. **Clear the target gate.** Flip the team out of "migrating" mode; the target's
   `poll_scheduled_messages` resumes from the synced state. Because source firing was suspended and the
   final run captured the source's last firing state, no scheduled occurrence is duplicated or
   skipped.

### Re-establishment checklist

Cutover also surfaces the resources that are re-established rather than migrated (see the
`KNOWN_EXCLUSIONS` set in the *Model classification* table), so nothing is silently dropped:

- **OAuth2 applications** — the team's `OAuth2Application` registrations to recreate on the target;
  external API clients re-consent (client secrets are hashed and tokens are bearer secrets, so neither
  is migrated).
- **Social login / MFA** — users relying on social login or MFA re-authenticate / re-enrol.
- **API keys** — hashed on the source, so re-issued on the target.
- **Slack installs** — re-added (channel re-registration is manual).
- **Email / CommCare Connect** — instance-level inbound routing, handled out of band by ops.

## Read API Reference

The full schema reference for the two source-side read endpoints the sync command consumes — the
**manifest** and the **generic content-type slug** endpoint. Per the timestamp rule, every row from a
`BaseModel`-derived model additionally carries `created_at`/`updated_at`; these are omitted from the
schemas below except where they are load-bearing (the live-data cursors).

### 1. `GET /api/v2/sync/manifest/`

Returns the ordered list of content types to pull, with the per-type config the caller needs. The
caller goes through the entries in order — there is no hardcoded order in the command. The manifest is
also the **allowlist**: the slug endpoint refuses any content type not listed here, so the generic
endpoint can't expose a model it shouldn't.

**Response:**

```json
{
  "entries": [
    { "slug": "teams.team",                    "phase": "structural",      "cursor": "pk",            "secret": false },
    { "slug": "service_providers.llmprovider",  "phase": "structural",      "cursor": "pk",            "secret": true },
    { "slug": "experiments.experiment",         "phase": "structural",      "cursor": "pk",            "secret": false, "order_by": "working_version_id_nulls_first" },
    { "slug": "documents.collectionfile",       "phase": "structural",      "cursor": "pk",            "secret": false, "through": true },
    { "slug": "files.file",                     "phase": "structural+live", "cursor": "pk",            "secret": false },
    { "slug": "experiments.participant",        "phase": "live",            "cursor": "updated_at_id", "secret": true },
    { "slug": "chat.chatmessage",               "phase": "live",            "cursor": "pk",            "secret": false }
  ]
}
```

Per-entry config drives the caller generically:

- `phase` — `structural` (synced once), `live` (synced as a delta on each run), or `structural+live`
  (the single `files.file` entry, synced in both passes).
- `cursor` — `pk` (filter `id > cursor`, for append-only/created-once rows) or `updated_at_id` (filter
  `(updated_at, id) > (cursor_ts, cursor_id)`, for mutable rows, so each rerun re-pulls rows that
  changed since the previous run).
- `secret` — when true, the caller passes `?public_key=<base64>` and expects the model's
  `SECRET_REGISTRY` fields to come back sealed. The flag is **derived** from `SECRET_REGISTRY`
  membership, not declared twice (a test asserts the two agree — see *guard tests*).
- `order_by` — overrides the default `id` ordering; `working_version_id_nulls_first` is used by
  `experiments.experiment` and `pipelines.pipeline` so every working version precedes any published
  version that references it.
- `through` — marks an explicit through model (extra columns) so the caller creates it via its own row
  rather than as a bare M2M id list.

No exclusion list is served; completeness is enforced by a test instead (see *guard tests*).

### 2. `GET /api/v2/sync/<app_label.model>/?cursor=<keyset>&limit=<n>[&public_key=<base64>]`

Resolves the content type and returns that model's team-scoped rows, paginated. Each response holds
**one model's rows only** — nothing nested. How a model is scoped to the team comes from
`TEAM_PATH_REGISTRY` (see *Serialization*): a model with its own `team` FK (anything extending
`BaseTeamModel`, plus `trace.trace`) filters on `team=team`; a model without one (`BaseModel` / plain
`Model` — e.g. `chat.chatmessage`, `pipelines.pipelinechatmessages`, `analysis.analysisquery`) filters
through a **declared ORM lookup path** to a parent that has a `team` (e.g. `chat__team`,
`chat_history__session__team`, `analysis__team`).

**Query params:** `cursor` (keyset value, omitted on the first page); `limit` (page size);
`public_key` (base64 DER, required for `secret: true` slugs).

**Response envelope:**

```jsonc
{
  "next_cursor": 5000,        // int for cursor=pk; { "updated_at": "...", "id": N } for cursor=updated_at_id
  "has_more": true,
  "results": [ /* one model's serialized rows */ ]
}
```

`next_cursor` is the last keyset value in the response; `has_more` indicates more rows beyond it.
Append-only/created-once slugs (`cursor: pk`) filter `id > cursor` ordered by `id` — immutable and
monotonic, so no boundary overlap. Mutable slugs (`cursor: updated_at_id`) filter
`(updated_at, id) > (cursor_ts, cursor_id)` ordered by the same composite, so a run of rows sharing one
`updated_at` is paged deterministically — never skipped (truncation) nor re-served forever (timestamp
ties). Upsert-by-source-pk keeps any re-served boundary row a no-op.

**Row shape** (produced by the generated `ModelSerializer` — see *Serialization*):

- The row's own primary key stays `id`.
- A **foreign key** serializes under its relation name with the source pk as the value (`consent_form`,
  not `consent_form_id`) — DRF's default. The importer remaps these via `FKTranslation`.
- A **bare M2M** serializes to a pk list under its relation name — exactly the id list the importer
  feeds to `.set()`.
- Datetimes, JSON, and Decimals get DRF's standard coercion. `created_at`/`updated_at` are present on
  every `BaseModel` row.
- Secret fields come out sealed (see [Secrets](#secrets)).

**Example responses** illustrating the shape variants:

```jsonc
// Secret-bearing structural row (service_providers.llmprovider) — FK as relation name, config sealed
GET /api/v2/sync/service_providers.llmprovider/?public_key=<base64>
{ "next_cursor": 1, "has_more": false,
  "results": [ { "id": 1, "name": "OpenAI Prod", "type": "openai",
                 "config": "<sealed under public_key>", "team": 1,
                 "created_at": "...", "updated_at": "..." } ] }
```

```jsonc
// Versioned row (experiments.experiment) — working_version self-FK, working-first ordering
GET /api/v2/sync/experiments.experiment/?cursor=0
{ "next_cursor": 24, "has_more": true,
  "results": [ { "id": 20, "name": "Customer Support Bot", "version_number": 0,
                 "working_version": null,
                 "consent_form": 3, "pre_survey": null, "post_survey": 9,
                 "voice_provider": 4, "synthetic_voice": 6, "trace_provider": null,
                 "pipeline": 42, "team": 1, "created_at": "...", "updated_at": "..." } ] }
```

```jsonc
// Pipeline graph (pipelines.pipeline) — react-flow graph (incl. edges) lives in the data JSON field
GET /api/v2/sync/pipelines.pipeline/?cursor=0
{ "next_cursor": 42, "has_more": true,
  "results": [ { "id": 42, "name": "Support Flow", "version_number": 0, "working_version": null,
                 "data": { "nodes": [ /* ... */ ], "edges": [ /* ... */ ] }, "team": 1 } ] }
```

```jsonc
// Explicit through model (documents.collectionfile, through: true) — extra columns preserved
GET /api/v2/sync/documents.collectionfile/
{ "next_cursor": 77, "has_more": false,
  "results": [ { "id": 77, "collection": 10, "file": 11,
                 "status": "completed", "external_id": "", "metadata": {} } ] }
```

```jsonc
// Bare M2M carried as a pk list (chat.chatattachment.files), applied with .set() on import
GET /api/v2/sync/chat.chatattachment/
{ "next_cursor": 80, "has_more": false,
  "results": [ { "id": 80, "chat": 71, "tool_type": "code_interpreter",
                 "extra": {}, "files": [501] } ] }
```

```jsonc
// Mutable live slug (experiments.experimentsession) — composite cursor
GET /api/v2/sync/experiments.experimentsession/
{ "next_cursor": { "updated_at": "2026-06-09T14:32:00Z", "id": 30 }, "has_more": true,
  "results": [ { "id": 30, "experiment": 20, "participant": 12, "experiment_channel": 42,
                 "chat": 71, "status": "pending", "session_data": {},
                 "created_at": "...", "updated_at": "..." } ] }
```

### Serialization

Each content type's rows are produced by a **dynamically built DRF `ModelSerializer`** — one per model
from a factory, not dozens of hand-written classes — so a serializer can't drift from its model and a
new field is exported the moment it's added. The serializers are output-only; `.save()` is never
called. The factory leans on `ModelSerializer`'s defaults (own pk → `id`; FK → relation name + pk;
bare M2M → pk list; standard datetime/JSON/Decimal coercion) rather than fighting them.

Three registries, co-located with the manifest and maintained by code review, are the only per-model
surface:

- `EXCLUDE_REGISTRY` — model → fields to drop (`customuser.password`, `widget_version*` telemetry,
  soft-delete columns, excluded M2M). Passed as `Meta.exclude`. "Dump every field" is the default;
  this is the small, explicit set of exceptions.
- `SECRET_REGISTRY` — model → field names that must be sealed in transit.
- `TEAM_PATH_REGISTRY` — model → the ORM lookup path the endpoint filters on to scope its queryset to
  one team, applied as `Model.objects.filter(<path>=team)` (the pattern existing views already use,
  e.g. `ChatMessage.objects.filter(chat__team=team)`). The default is `"team"` — the direct FK on
  every `BaseTeamModel` and on `trace.trace` — so only the slugs *without* a `team` FK need an entry:
  `chat.chatmessage` / `chat.chatattachment` → `chat__team`; `pipelines.pipelinechathistory` →
  `session__team`; `pipelines.pipelinechatmessages` → `chat_history__session__team`;
  `evaluations.evaluationrunaggregate` → `run__team`; `analysis.analysisquery` → `analysis__team`. The
  path is declared, never auto-derived: a nullable parent FK silently drops rows whose path is null
  (`trace.trace.team` is itself nullable by design), and some models have several candidate parents, so
  each path is a reviewed decision. `evaluations.evaluationmessage` is the one model with no single
  clean path (see *Open questions*).

A few models need a value that isn't a plain field dump — `teams.team.feature_flags` (flag names),
`teams.membership.groups` (group names), and the `is_global` flag on matched global rows. Each is a
`SerializerMethodField` passed to the factory as an extra field; these are the only genuinely
per-model code.

**Module layout:** `apps/api/v2/sync/serializers.py` holds the factory and `_SyncSecretMixin`;
`apps/api/v2/sync/manifest.py` holds the manifest entries, `SECRET_REGISTRY`, `EXCLUDE_REGISTRY`, and
`TEAM_PATH_REGISTRY` — a single maintenance surface for the whole endpoint.

### Secrets

Each provider's `config` is a JSON object stored encrypted at rest with the source environment's key.
Rather than singling out individual sensitive keys, the export treats the **entire `config` object as
opaque**: it is decrypted with the source key and re-encrypted (sealed) with the target's public key
as one blob. `documents.documentsource.config`, `bot_channels.experimentchannel.extra_data`, and
`experiments.participantdata.data` / `encryption_key` are handled the same way. This keeps the source
and target code agnostic to each provider type's field layout.

These secrets fall into two classes: *encrypted at rest* — the five provider `config`s plus
`ParticipantData.data` / `encryption_key`, all detectable as `django_cryptography` `EncryptedMixin`
fields — and *plaintext at rest but sensitive by policy* — `documents.documentsource.config` and
`bot_channels.experimentchannel.extra_data`, which no field type marks as secret. Both classes are
declared in `SECRET_REGISTRY` and sealed by a single mixin shared across every generated serializer:

```python
class _SyncSecretMixin:
    def to_representation(self, instance):
        data = super().to_representation(instance)
        for field in self.secret_fields:               # from SECRET_REGISTRY
            data[field] = seal(getattr(instance, field), self.context["public_key"])
        return data
```

Because `encrypt()` (django-cryptography) decrypts transparently on read, both encrypted-at-rest and
plaintext-sensitive fields reach `to_representation` as plain Python values and take the identical
sealing path. `public_key` rides in the serializer context from the endpoint's query param; non-secret
models never read it.

To transfer a secret field:

1. The **target** generates one RSA keypair per sync run, before fetching the manifest.
2. The target passes its **public key** as `?public_key=<base64-DER>` on every `secret: true` slug
   call.
3. The **source** decrypts the field with its own env key and `seal`s it under the target's public
   key, returning the ciphertext as the field value.
4. The **target** unseals it with its private key and re-encrypts under its own env key on insert.

`seal` is **envelope encryption** — a random symmetric key encrypts the value and the RSA public key
wraps that symmetric key — because a value can exceed RSA's direct size limit (e.g. the Vertex AI
service-account JSON). The exact envelope format is an implementation detail; to the caller the field
is an opaque base64 string. Plaintext secret values never appear in the response body. The keypair is
ephemeral (generated per run and discarded after import). Fields outside `SECRET_REGISTRY` are sent as
plaintext — including a provider's `name`/`type` and a messaging provider's `extra_data` (which holds
only derived values such as Meta's `verify_token_hash`).

### Authorization

The `/api/v2/sync/` endpoints (the manifest and the generic slug endpoint) require a
**superuser-level or dedicated migration credential**. They expose the full team's data including
sealed secrets, so they must be tightly authorized, strictly team-scoped, and audited. See
`docs/agents/django_view_security.md`. The generic slug endpoint additionally enforces the manifest
allowlist — a content type not in the manifest is rejected, so the endpoint cannot be coaxed into
serving an unclassified model.

### Guard tests

**Exhaustiveness guard.** A test enumerates `apps.get_models(include_auto_created=True)` — the
`include_auto_created` flag is required, since auto M2M through tables are otherwise invisible —
subtracts the manifest's slugs, and asserts the remainder equals a `KNOWN_EXCLUSIONS` set declared in
the test (each member commented with its reason). A new, unclassified model fails the test until it is
either added to the manifest or to `KNOWN_EXCLUSIONS`. This is the executable counterpart of the
*Model classification* table, modelled on
`apps/teams/tests/test_permissions.py::test_missing_content_types`.

**Secret tripwire.** Three checks guard against leaking secrets as plaintext:

- *Registry ⇄ manifest agreement.* A test asserts each manifest entry's `secret` flag equals
  `SECRET_REGISTRY` membership for that model, so the two can't drift.
- *Encrypted-at-rest coverage.* A test enumerates every `EncryptedMixin` field across the synced
  models and asserts each appears in `SECRET_REGISTRY` — an encrypted-at-rest field can't be added
  without being sealed. A behavioural test then runs each synced model through its serializer and
  asserts every `SECRET_REGISTRY` field comes out as ciphertext, never plaintext (the library decrypts
  transparently on read, so naive serialization *would* leak).
- *Sensitive-plaintext baseline.* The plaintext-sensitive fields
  (`bot_channels.experimentchannel.extra_data`, `documents.documentsource.config`) can't be detected
  by type. A field-snapshot baseline over the secret-carrying models trips CI whenever a field is added
  to one of them, forcing a classify-or-acknowledge decision (add to `SECRET_REGISTRY`, or bump the
  baseline).

### Model classification

Every registered model is accounted for below: a **synced** row (a manifest entry) or **excluded**
(with a reason that lives in the test's `KNOWN_EXCLUSIONS`). The synced rows are numbered 1–62 in call
order; the excluded list continues 63–134 — one continuous run over every registered model
(`apps.get_models(include_auto_created=True)` = 134), so a reader can confirm nothing is unaccounted
for. Cursor rule: structural data is pulled once from an unchanging source, so `pk`; live append-only
is `pk`; live mutable uses `updated_at_id` so a rerun re-pulls rows that changed since the previous
run.

#### Synced (manifest entries, in call order)

| # | slug | phase | cursor | secret | notes |
|---|---|---|---|---|---|
| 1 | `teams.team` | structural | pk | | singleton; `feature_flags` = flag names; carries the team↔flag link |
| 2 | `users.customuser` | structural | pk | | no password |
| 3 | `teams.membership` | structural | pk | | role + group names (groups matched by name) |
| 4 | `service_providers.llmprovider` | structural | pk | ✓ | config |
| 5 | `service_providers.voiceprovider` | structural | pk | ✓ | config |
| 6 | `service_providers.messagingprovider` | structural | pk | ✓ | config secret; `extra_data` plaintext |
| 7 | `service_providers.authprovider` | structural | pk | ✓ | config |
| 8 | `service_providers.traceprovider` | structural | pk | ✓ | config |
| 9 | `service_providers.llmprovidermodel` | structural | pk | | team + global; global matched by `type`+`name`(+`max_token_limit`), not recreated |
| 10 | `service_providers.embeddingprovidermodel` | structural | pk | | team + global; global matched, not recreated |
| 11 | `experiments.syntheticvoice` | structural | pk | | team + global; global matched, not recreated |
| 12 | `custom_actions.customaction` | structural | pk | | refs `auth_provider` |
| 13 | `custom_actions.customactionoperation` | structural | pk | | node-attached operations |
| 14 | `experiments.sourcematerial` | structural | pk | | |
| 15 | `experiments.consentform` | structural | pk | | |
| 16 | `experiments.survey` | structural | pk | | |
| 17 | `annotations.tag` | structural | pk | | tag definitions |
| 18 | `documents.collection` | structural | pk | | |
| 19 | `files.file` | structural+live | pk | | one slug for all files; pulled early (collection files), re-polled live (attachment files); content fetched separately |
| 20 | `documents.collectionfile` | structural | pk | | through (status/external_id/metadata); after collection + files |
| 21 | `documents.documentsource` | structural | pk | ✓ | `config` (plaintext-sensitive, sealed) |
| 22 | `files.filechunkembedding` | structural | pk | | bulk vectors; after files + collections |
| 23 | `ocs_notifications.eventtype` | structural | pk | | |
| 24 | `ocs_notifications.usernotificationpreferences` | structural | pk | | refs user |
| 25 | `pipelines.pipeline` | structural | pk | | `order_by` working-first; graph (incl. edges) in `data` |
| 26 | `pipelines.node` | structural | pk | | node `params` carry soft-FK refs |
| 27 | `experiments.experiment` | structural | pk | | chatbots; `order_by` working-first |
| 28 | `bot_channels.experimentchannel` | structural | pk | ✓ | `extra_data` (plaintext-sensitive); webhook re-registration manual |
| 29 | `events.eventaction` | structural | pk | | referenced by triggers + scheduled messages |
| 30 | `events.statictrigger` | structural | pk | | |
| 31 | `events.timeouttrigger` | structural | pk | | keeps `config_changed_at` |
| 32 | `experiments.participant` | live | updated_at_id | | |
| 33 | `experiments.participantdata` | live | updated_at_id | ✓ | `data` + `encryption_key` encrypted |
| 34 | `chat.chat` | live | pk | | before session (`ExperimentSession.chat` is a OneToOne to `Chat`); created-once → pk |
| 35 | `experiments.experimentsession` | live | updated_at_id | | refs experiment / participant / chat / channel |
| 36 | `chat.chatattachment` | live | pk | | carries remapped `files` (bare M2M) |
| 37 | `chat.chatmessage` | live | pk | | append-only |
| 38 | `trace.trace` | live | pk | | append-only |
| 39 | `pipelines.pipelinechathistory` | live | updated_at_id | | |
| 40 | `pipelines.pipelinechatmessages` | live | pk | | append-only |
| 41 | `events.scheduledmessage` | live | updated_at_id | | refs participant / experiment / eventaction |
| 42 | `ocs_notifications.notificationevent` | live | pk | | append-only |
| 43 | `ocs_notifications.eventuser` | live | updated_at_id | | inbox read-state |
| 44 | `evaluations.evaluator` | live | updated_at_id | | judge config |
| 45 | `evaluations.evaluationmessage` | live | updated_at_id | | `BaseModel`, **no clean team path** — `session` / `input_chat_message` are both nullable; needs a disjunction-or-exclude decision (see *Open questions*) |
| 46 | `evaluations.evaluationdataset` | live | updated_at_id | | `messages` M2M → id list |
| 47 | `evaluations.datasetautopopulationrule` | live | updated_at_id | | refs dataset + `source_experiment` |
| 48 | `evaluations.evaluationconfig` | live | updated_at_id | | `evaluators` M2M → id list; refs dataset + experiment versions |
| 49 | `evaluations.evaluatortagrule` | live | updated_at_id | | refs evaluator + `annotations.tag` |
| 50 | `evaluations.evaluationrun` | live | updated_at_id | | `scoped_messages` M2M → id list; refs config / experiment / user |
| 51 | `evaluations.evaluationresult` | live | pk | | append-only; refs evaluator / message / run / session |
| 52 | `evaluations.evaluationrunaggregate` | live | updated_at_id | | `BaseModel` (team via run); refs run + evaluator |
| 53 | `evaluations.appliedtag` | live | pk | | append-only; refs result / rule / tag |
| 54 | `human_annotations.annotationqueue` | live | updated_at_id | | `assignees` M2M (users) → id list |
| 55 | `human_annotations.annotationitem` | live | updated_at_id | | refs queue / session / message |
| 56 | `human_annotations.annotation` | live | updated_at_id | | refs item / reviewer |
| 57 | `human_annotations.annotationqueueaggregate` | live | updated_at_id | | OneToOne queue |
| 58 | `analysis.transcriptanalysis` | live | updated_at_id | | `sessions` M2M → id list; refs experiment + llm providers |
| 59 | `analysis.analysisquery` | live | pk | | plain `Model`, no timestamps (no cursor-ts, no timestamp write-back); refs analysis |
| 60 | `annotations.customtaggeditem` | live | pk | | generic FK; near last |
| 61 | `annotations.usercomment` | live | updated_at_id | | generic FK; near last |
| 62 | `assessments.score` | live | updated_at_id | | generic FK (target) + refs automated_result / review; **last** |

#### Excluded (`KNOWN_EXCLUSIONS`, grouped by reason)

Numbering continues from the manifest: 63–134. Every group is fully enumerated (no wildcards) so the
run is gapless — 1–134 covers all 134 registered models exactly once.

- **Excluded by decision** (referencing nodes keep a dangling FK):
  - 63. `assistants.openaiassistant`
  - 64. `assistants.toolresources`
  - 65. `mcp_integrations.mcpserver`
- **Auth/identity, re-established on target**:
  - 66. `account.emailaddress`
  - 67. `account.emailconfirmation`
  - 68. `mfa.authenticator`
  - 69. `oauth.oauth2accesstoken`
  - 70. `oauth.oauth2application`
  - 71. `oauth.oauth2grant`
  - 72. `oauth.oauth2idtoken`
  - 73. `oauth.oauth2refreshtoken`
  - 74. `oauth2_provider.devicegrant`
  - 75. `socialaccount.socialaccount`
  - 76. `socialaccount.socialtoken`
  - 77. `api.userapikey`
  - 78. `rest_framework_api_key.apikey`
  - 79. `sso.ssosession`
- **Channel re-registration is manual**:
  - 80. `slack.slackoauthstate`
  - 81. `slack.slackinstallation`
  - 82. `slack.slackbot`
- **Logs / operational / transient**:
  - 83. `events.eventlog`
  - 84. `events.scheduledmessageattempt`
  - 85. `documents.documentsourcesynclog`
  - 86. `experiments.promptbuilderhistory`
  - 87. `admin.logentry`
  - 88. `field_audit.auditevent`
  - 89. `silk.profile`
  - 90. `silk.request`
  - 91. `silk.response`
  - 92. `silk.sqlquery`
  - 93. `django_celery_beat.clockedschedule`
  - 94. `django_celery_beat.crontabschedule`
  - 95. `django_celery_beat.intervalschedule`
  - 96. `django_celery_beat.periodictask`
  - 97. `django_celery_beat.periodictasks`
  - 98. `django_celery_beat.solarschedule`
  - 99. `debug_toolbar.historyentry`
  - 100. `data_migrations.custommigration`
  - 101. `sessions.session`
- **Global / instance config, seeded on target** (matched by name, not recreated):
  - 102. `auth.permission`
  - 103. `auth.group`
  - 104. `contenttypes.contenttype`
  - 105. `sites.site`
  - 106. `site_admin.ocsconfiguration`
  - 107. `banners.banner`
  - 108. `waffle.sample`
  - 109. `waffle.switch`
  - 110. `socialaccount.socialapp`
  - (`waffle.flag` is **not** registered — OCS swaps in `teams.flag` (#111) as the flag model)
- **Carried on another row, not its own slug**:
  - 111. `teams.flag` (the team↔flag link is carried on the team row as `feature_flags`)
- **User UI state**:
  - 112. `dashboard.dashboardcache`
  - 113. `dashboard.dashboardfilter`
  - 114. `filters.filterset`
- **Superseded / not used directly**:
  - 115. `taggit.tag` (concrete table is `annotations.tag`)
  - 116. `taggit.taggeditem` (concrete table is `annotations.customtaggeditem`)
- **Pending**:
  - 117. `teams.invitation` (open invites not migrated)
- **Auto-created M2M through tables** (the guard sees these via `include_auto_created=True`). Bare M2M
  are carried as id-list fields on their owning synced row, so they need no slug; the rest are excluded
  outright — group/permission membership is re-established on the target:
  - _Carried as an id-list on the owning row:_
    - 118. `analysis.transcriptanalysis_sessions` → on `analysis.transcriptanalysis` (#58)
    - 119. `chat.chatattachment_files` → on `chat.chatattachment` (#36)
    - 120. `evaluations.evaluationconfig_evaluators` → on `evaluations.evaluationconfig` (#48)
    - 121. `evaluations.evaluationdataset_messages` → on `evaluations.evaluationdataset` (#46)
    - 122. `evaluations.evaluationrun_scoped_messages` → on `evaluations.evaluationrun` (#50)
    - 123. `human_annotations.annotationqueue_assignees` → on `human_annotations.annotationqueue` (#54)
  - _Excluded outright (owner excluded, or membership re-established on target):_
    - 124. `assistants.toolresources_files` (owner `assistants.toolresources` excluded, #64)
    - 125. `auth.group_permissions`
    - 126. `silk.profile_queries`
    - 127. `socialaccount.socialapp_sites`
    - 128. `teams.flag_groups`
    - 129. `teams.flag_teams` (the team↔flag link, carried on the team row, #1)
    - 130. `teams.flag_users`
    - 131. `teams.invitation_groups`
    - 132. `teams.membership_groups`
    - 133. `users.customuser_groups`
    - 134. `users.customuser_user_permissions`

## Design rationale & trade-offs

Why a single content-type slug endpoint driven by a server-side manifest:

- **No unbounded payload** — everything is paginated.
- **Coverage and testability** — the guard test maps one-to-one to the manifest; nothing is hidden
  inside a bundle.
- **Simple to follow and audit** — one call, one model.
- **One `files` slug** polled in both phases — no files/attachment-files split.
- **Server owns the order** (manifest), so the command is generic and a newer source can add models
  without a command change.
- **The manifest is the allowlist** — removes the risk of the generic endpoint exposing too much.

The costs accepted in exchange:

- **New pattern** — v2 today is router + ViewSets; a content-type dispatch view with per-type
  cursor/secret config and strict team-scoping is new to the codebase.
- **More round-trips** — many ordered, paginated calls.
- **No atomic bundles** — a chatbot, its channels, and its events arrive in separate calls, so a
  half-applied chatbot is a transient state between calls (checkpoint/resume covers it).
- **The manifest declares only what/order**; the caller still owns the *how* (FK remap, secrets,
  through handling). Fine while the flag set stays small — a warning sign if every model needs a
  special flag.

## Addendum

### FK Translation Table

```python
class ForeignKeyTranslation():
    content_type: chat
    source_key: int
    target_key: int[nullable]
```

### Checkpointing

The `ForeignKeyTranslation` table acts as a checkpoint table. An empty `target_key` indicates we
haven't synced that resource yet. We should be able to rerun everything to continue.

## Questions

### Resolved

- **Keeping the API current as models are added** — a guard test enumerates
  `apps.get_models(include_auto_created=True)`, subtracts the manifest's slugs, and asserts the
  remainder equals `KNOWN_EXCLUSIONS`; serialization is a field-introspection-driven `ModelSerializer`
  factory so new fields ride along; a secret tripwire (registry⇄manifest agreement,
  encrypted-at-rest coverage, sensitive-plaintext baseline) guards secrets. See *Guard tests*.
- **Versioning** — `experiments.experiment` and `pipelines.pipeline` are pulled
  `order_by working_version_id NULLS FIRST, id`, so every working version precedes any published
  version that references it via `working_version`.
- **Scheduled messages** — synced as live data (`events.scheduledmessage`) with a per-team firing gate
  so the target does not double-fire during the migration window. See *Scheduled-message firing gate*.
- **Paginating live data** — per-slug keyset pagination set by the manifest `cursor` field (`pk` for
  append-only, `updated_at_id` for mutable), consumed in manifest order, with each slug's cursor
  persisted locally between runs. See the slug endpoint reference.
- **Re-syncing live data** — the command is single-pass: each run ensures the structural data is
  present (idempotent) and then syncs the live data once, as a delta from the persisted cursors, then
  exits. There is no internal polling loop; to take another iteration the operator simply reruns the
  command. See *The sync process*.
- **OAuth models** — deferred; re-established on the target via a cutover checklist (same bucket as
  Slack/webhook re-registration), since no synced model depends on them and there is no OAuth
  auth-provider type. See *Cutover* and `KNOWN_EXCLUSIONS`.
- **Chatbot switchover** — a single team-wide cutover, not bot-by-bot: auto-flip what we can
  (Telegram/Twilio), report the provider-level flips (Meta/Slack/SureAdhere) and per-channel manual
  flips (Turn.io/Web/API), and treat email + CommCare Connect as instance-level ops. The firing gate
  stays per-team. See *Cutover*.

### Open

- **Team-scoping `evaluations.evaluationmessage`** — unlike every other live model it has no single
  non-null path to a team: `session`, `input_chat_message`, and `expected_output_chat_message` are all
  nullable (null for CSV-imported or manually-created rows). So a single `TEAM_PATH_REGISTRY` lookup
  like `session__team` would silently drop those rows. Two options:
  1. **Disjunction** — filter on `Q(session__team=team) | Q(input_chat_message__chat__team=team)`,
     accepting that rows where *all* parents are null still can't be reached through a path (they would
     need a different handle, e.g. the `evaluationdataset.messages` M2M, or be excluded).
  2. **Exclude** — drop `evaluationmessage` from the manifest into `KNOWN_EXCLUSIONS` for now, treating
     evaluation messages as out of scope until a clean scoping rule is decided.

  `TEAM_PATH_REGISTRY` would need to allow a `Q`-expression (not just a string path) for option 1.
