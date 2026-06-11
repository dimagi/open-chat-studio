---
status: active
---

# Team Data Sync — Overview

## Glossary

- **Source Server**: The server being migrated from.
- **Target Server**: The server being migrated to.

## Scope & prerequisites

The only source-side prerequisites are a team admin, an API key, and arming the migration lock
(see *End-to-end lifecycle*, *Migration lock*, and *Authorization*).

## Overview

The migration runs as a management command on the target server. It fetches a team's data from the
source over HTTPS and recreates it locally through the ORM.

The command hardcodes no model order. Instead it fetches a manifest that lists every content type to
pull, in dependency order, along with the config each type needs. For each entry it pages through a
generic content-type endpoint, and for every row it resolves the foreign keys, creates the row
locally, and records an FK translation. Pull, translate FKs, create, record: that loop is the whole
sync. Each run makes one pass and exits; to pick up new live data, rerun the command (see *The sync
process*).

It uses two new read endpoints on the source, both under the `/api/v2/sync/` prefix, plus the
existing Files API:

- **Manifest** (`GET /api/v2/sync/manifest/`): the ordered list of content types to pull, each with
  its config (`phase`, `cursor`, `secret`, `order_by`, `through`). The manifest is both the call order
  and the allowlist: the command follows it rather than a hardcoded order, and the slug endpoint
  refuses any content type not listed.
- **Content-type slug** (`GET /api/v2/sync/<app_label.model>/?cursor=<keyset>&limit=<n>[&public_key=<base64>]`):
  one model's team-scoped rows, paginated. A response carries rows from that one model only, nothing
  nested.
- **Files API** (existing): fetches a file's bytes given its metadata.

## End-to-end lifecycle

The migration is mostly hands-off, bracketed by two manual moments: a team admin arms the migration
lock at the start, and the operator runs cutover at the end. Each step has its own section below;
this is the map of what to do, in order.

1. **Provision the target.** Stand up and configure the self-hosted instance per the hosting
   docs.
2. **Mint a source API key.** On the source, a team admin creates an API key for the team
   (read-only is enough, since every sync endpoint is a `GET`). See *Authorization*.
3. **Arm the migration lock.** The team admin flips the team into migration mode from the
   source's team settings. This freezes structural changes and stops the source firing
   scheduled messages for that team, so the data can't drift while it is being pulled. See
   *Migration lock*.
4. **Run the sync.** On the target:
   `manage.py sync_team --source-url=<src> --api-key=<key> [--private-key-path=<path>]` (the team
   is implicit in the key).
   The first call fetches the manifest, prepares the run's RSA keypair (generated ephemerally under
   Approach 1, or loaded from `--private-key-path` under Approach 2; see *Authorization*), builds
   the team (setting `is_migrating` on the target so its synced scheduled messages stay inert), and
   pages through every manifest entry. See *The sync process*.
5. **Parity check.** Confirm no `FKTranslation` row has a null `target_key`. File bytes move out of
   band via the bulk zip, so verify file content separately: an object-count and total-byte-size
   check plus a spot-check. See *Parity check*.
6. **Cutover.** Re-point inbound webhooks to the target (auto for Telegram/Twilio; the command
   prints the manual list for the rest), do a final run once the source is quiet, then clear the
   lock on the target so it resumes firing. Work the re-establishment checklist. See *Cutover*.
   The source team stays locked and is abandoned.

## Setup

Before the first call, a team admin arms the migration lock on the source (see *Migration lock*); the
sync endpoints refuse to serve a team that isn't in migration mode. Arming the lock enforces the "no
structural changes on the source while migrating" rule: it blocks the main structural-creation paths
and stops the source firing scheduled messages for the team, while leaving live chat traffic
untouched.

Running the command fetches the manifest, prepares the run's RSA keypair (see *Authorization* for the
two key-handling approaches), and creates a local SQLite DB for the run with an empty `FKTranslation`
table.

**State persistence (SQLite).** The SQLite DB holds the `FKTranslation` table and the per-slug
cursors, so it must live on a persistent, mounted path. If the target runs in an ephemeral container,
an unmounted file means resume silently loses all translation state between reruns. The DB is
eventually named for the team slug, but the slug isn't known until the `teams.team` row is synced, so
the run starts under a provisional name keyed on the source URL (e.g. a hash) and renames to the team
slug once that row arrives. The `FKTranslation` table lives in SQLite while the synced rows live in
the target's Postgres, so "create row" and "record translation" can't share a transaction. A crash
between the two is expected, and rerun resolves it via the identity rules in *The sync process*
(primary `FKTranslation` lookup, with a natural-key safety net).

**FK translation rule.** Every source row we sync gets an `FKTranslation` row keyed by
`(content_type, source_key)`, with `target_key` left null until the row exists on the target. Every
foreign-key field in every response is resolved through this table, and creating a row fills in its
`target_key`. The table also serves as the checkpoint (see *Parity check*), which is what lets the
command resume on a rerun.

**Timestamp preservation rule.** Every record from the slug endpoint carries the source row's
`created_at` and `updated_at` (for every model that has those fields, i.e. anything extending
`BaseModel`, which defines them as `auto_now_add`/`auto_now`). Those field options ignore any value
passed to `save()`, so the timestamps can't be set at creation time. `QuerySet.update()` skips
`pre_save` and bypasses `auto_now`/`auto_now_add`, so right after creating (or upserting) each row the
command runs `Model.objects.filter(pk=...).update(created_at=..., updated_at=...)` to write the source
values back onto the new row, no raw SQL needed. This applies to every resource and keeps the target's
timestamps faithful to the source. The response examples below show these fields on a couple of rows;
they're present on all records and omitted elsewhere for brevity.

## The sync process

The import logic lives in a standalone, unit-testable importer engine
(`apps/api/v2/sync/importer.py`), not in the management command. The command is a thin shell that
wires the source client to the engine and the local databases, so the FK-remap, JSON-ref, generic-FK,
and secret-unseal transforms can be tested as plain functions (see *Testing strategy*).

After fetching the manifest and preparing the run's RSA keypair, the command walks the manifest
entries in order. For each entry it pages through `GET /api/v2/sync/<slug>/` (passing the public key
when the entry is `secret: true`; see *Authorization* for how that key is supplied), and for every
row it:

1. **Resolve foreign keys.** Each FK field value is a *source* pk; the command looks it up in
   `FKTranslation` to get the target pk (FK handling detailed below).
2. **Create or update** the row locally. `FKTranslation` is the primary identity: on a rerun the
   command looks up `(content_type, source_pk)`. If `target_key` is set it updates that row (mutable
   slugs) or skips it (append-only slugs); otherwise it creates it. Mutable slugs use
   `update_or_create` (not `get_or_create`) so a re-pulled changed row actually updates rather than
   staying stale. A natural-key `update_or_create` is the safety net for the rare crash window where a
   row was created but its `FKTranslation.target_key` write didn't land (the two live in different
   databases; see *Setup* and *Checkpointing*). A few models have no natural key
   (`chat.chatmessage`, `trace.trace`, `pipelines.pipelinechatmessages`,
   `evaluations.evaluationresult`, and the generic-FK rows); for these the window can't be closed, so
   a rerun could duplicate a row. Any FK used inside a natural key is translated first. After
   create/update, the command records the `(content_type, source_pk) → target_pk` mapping in
   `FKTranslation`.
3. **Write the timestamps back.** An ORM `.update()` (which bypasses `auto_now`/`auto_now_add`)
   restores the source `created_at`/`updated_at` (timestamp rule). Models with no timestamp fields
   skip this step, detected by field introspection rather than a hardcoded name check;
   `analysis.analysisquery` (a plain `Model`) is the current case.

The manifest order is the dependency order: a referencing model's slug always follows the slug of
whatever it points at, so every FK resolves against an already-created row. Since `FKTranslation` also
serves as the checkpoint (a null `target_key` means "not yet created"), the command can be re-run at
any time to resume where it left off.

Each manifest entry carries a `phase`:

- **`structural`**: synced once. The team's stable building blocks and configuration: the team, users,
  memberships, service providers and their models/voices, custom actions, source materials, consent
  forms, surveys, tags, collections, document sources, notification config, pipelines, nodes,
  chatbots, channels, and events.
- **`live`**: synced as a delta on each run. The tables that grow with chatbot interactions:
  participants, sessions, chats, messages, traces, pipeline chat history, scheduled messages,
  notifications, evaluations, human annotations, transcript analysis, annotations, and assessment
  scores. These rows keep changing on the source during the migration, so a run pulls whatever is new
  or changed since the previous run.
- The single `files.file` entry is **`structural+live`**: synced in both passes (collection-backed
  files in the structural pass, chat-attachment-backed files in the live pass).

**The command runs as a single pass; it does not loop.** One run pulls all structural entries to
build the team, then syncs every live entry once as a delta, and exits. Structural is idempotent, so
on a rerun its already-created rows are skipped via the checkpoint and that pass is a quick no-op
before the live entries are re-synced. To take another iteration of the live data, rerun the command.
There is no internal polling loop or scheduler. Each live slug's cursor is persisted in the local DB,
so every rerun resumes that slug's delta where the previous run ended; it never re-pulls from the
start. The operator reruns as often as needed during the migration, with the final rerun after cutover
freezes the source (see *Cutover*).

### Error handling, retries, and cursor advance

The command makes thousands of HTTP calls over a potentially long window, so its failure behaviour
is explicit:

- **Transient transport errors** (timeouts, HTTP 5xx, connection resets) are retried with backoff
  before the run aborts, so a single network blip doesn't kill a long migration.
- **Unresolvable references are handled by intent.** A reference to a model the sync deliberately
  skips (assistant, MCP server) resolves to nothing and is left null by design (see *Foreign keys and
  many-to-many*). An unexpected unresolvable required FK, a non-null column whose target wasn't created
  (e.g. a manifest-ordering bug), is a hard error: the command fails loudly rather than inserting a
  broken or silently-nulled row.
- **Cursor advance is per-page, after commit.** Each slug's cursor only advances once a page's rows
  are fully committed locally, so a mid-page crash re-pulls that whole page on rerun; the identity
  rules (primary `FKTranslation` lookup plus natural-key safety net) make the re-pull idempotent.

### Foreign keys and many-to-many

Every row arrives carrying primary keys (ids) from the source server. Those numbers mean nothing on
the target, whose rows were created fresh with their own ids. So before the command can create a row,
it has to rewrite every reference inside that row from the source id to the matching target id, which
it finds by looking the source id up in `FKTranslation`. The hard part isn't the rewrite; it's
*finding* every reference, because references show up in three forms.

**1. Normal foreign-key columns**, for example `Experiment.consent_form`: an ordinary database column
holding the id of a related row.

The command keeps no hand-written list of "which fields on which models are foreign keys"; such a list
would go stale the moment someone adds a field. Instead it inspects each model at runtime. Django
reports every field on a model (via `model._meta.get_fields()`) along with its type, and for each
field that's a foreign key the command:

  1. reads the source id stored in that column,
  2. determines which model the column points at, and
  3. looks up `(that model, source id)` in `FKTranslation` to get the target id, and writes it into
     the new row.

Because the field list comes from the model itself, a newly added foreign key is translated
automatically, with no code change. There's one deliberate gap: a foreign key pointing at a model this
sync doesn't copy (an assistant or an MCP server) has no `FKTranslation` entry, so it's left pointing
at nothing. That broken reference is expected and is handled separately, outside this sync.

**2. Foreign keys hidden inside JSON fields.** Some references aren't database columns at all; the id
sits inside a JSON value on the row, where the automatic field-by-field scan above can't find it. The
command handles these with a bit of purpose-built code, picked by the kind of row it just fetched:
when it pulls a pipeline or a pipeline node, it knows that row carries references inside its JSON and
runs the matching handler instead of the automatic scan.

There are two kinds of hidden reference, and they differ in one way: whether the JSON itself says
which model each id points at.

**Pipeline node settings.** A pipeline node keeps its settings in a JSON field called `params`, and a
pipeline keeps a copy of all its nodes' settings inside its graph (the `data` field). These settings
contain ids (an LLM provider, a source material, a collection, and so on) but never record which model
an id belongs to. That's decided by the name of the field the id sits under: an id under
`llm_provider_id` is always an LLM provider, one under `source_material_id` is always a source
material. Custom actions are the one special case: they're written as `"action_id:operation_id"`
strings rather than plain ids.

The inspect code already knows how to read these settings, and the sync reuses the parts that find
the references (all in `apps/api/v2/inspect/`):

- `RESOURCE_PARAM_FIELDS` lists every settings field that holds a reference and, for each, the kind of
  resource it points at.
- `ResourceKind.iter_raw_ids(value)` returns the ids inside a field's value, whether that value is a
  single id, a list of ids, or the `action_id:operation_id` form.
- `iter_resource_refs(node_type, params)` combines the two: for a given node it returns each reference
  as a `(kind, id)` pair, skipping fields the node doesn't use.

What this code doesn't give us today is the last step: from a kind to the model it points at. That
link currently lives only inside the team-scoped resource loader in
`apps/api/v2/inspect/resources.py` (its per-kind `_queryset()` methods). Rather than duplicate it, the
mapping is lifted into the inspect module as a single source of truth (`ResourceKind → model`) that
both the resource loader and the sync use; the sync layers its own rule on top for the kinds it
deliberately skips (e.g. `ASSISTANT`, which has no `FKTranslation` entry). A guard test asserts every
`ResourceKind` member is either mapped to a model or explicitly skipped, so a newly added kind can't
slip through (see *Guard tests*). With that mapping in hand, the handler walks every reference in a
node's settings: it reads the ids, looks each one up in `FKTranslation` under the kind's model to get
the target id, and writes the target ids back into the settings, keeping the `action_id:operation_id`
shape where it applies.

Two things follow. Because the list of reference fields is shared with the inspect code, a newly added
settings field is handled with no extra work. And because the kind-to-model mapping is shared too, a
newly added *kind* of resource is caught by the guard test until it's either mapped or explicitly
skipped. A reference to a resource the sync skips, such as an assistant, has no entry in
`FKTranslation`, so it's left unresolved, exactly as in case 1.

**Generic foreign keys.** A few models can point at *any* model rather than one fixed one: a tag
attached to something, a comment on something, a score on something. (Django calls this a generic
foreign key.) Such a reference is stored as two columns, one naming the model being pointed at and one
holding that row's id. Both are sent in the response, with the model as a readable `"app_label.model"`
string such as `"chat.chatmessage"` rather than its database id, which differs from one server to the
next.

Here the row already names its own model, so the handler needs no lookup table; it fills in the new
record's two columns directly:

1. **Find the target model.** Match the `"app_label.model"` string to the same model on the target.
   This is a plain match by name, since these model entries are built into Django and identical on
   both servers. The result becomes the new row's "which model" column.
2. **Translate the id.** Using that model and the source id, look up the matching target id in
   `FKTranslation` and write it into the new row's "which row" column.

With both columns set, the new record points at the correct row on the target.

**3. Many-to-many relationships**, for example a `ChatAttachment` linked to several `File` rows.

A many-to-many link can't be set while the row is being created, because both sides must already
exist. So the command always creates the row first and attaches its links afterwards (with Django's
`.set()`). How a link travels depends on whether the join between the two rows carries any extra data
of its own:

  - **Plain links (no extra columns).** Most many-to-many links are just "row A is connected to rows
    X, Y, Z." Django stores these in an auto-created join table that holds nothing but the two ids. The
    serializer emits the link as a list of target ids on the owning row; for example a
    `chat.chatattachment` row carries `"files": [501, 502]`. The command translates that list and calls
    `.set()` once the row exists. The join table is never requested on its own.
  - **Links that carry their own data**, for example `documents.collectionfile`, the join between a
    `Collection` and a `File`. Here the join row also stores fields of its own (`status`,
    `external_id`, `metadata`). A plain id list would discard that data, so this kind of join model is
    pulled as its own slug (marked `through: true` in the manifest) and created like any other row, so
    its extra columns are preserved.

### Versions (working before published)

`experiments.experiment` and `pipelines.pipeline` are pulled `order_by working_version_id NULLS
FIRST, id`. Working versions have a null `working_version`, so every working version comes before any
published one across the whole stream, which means the working version always exists before a
published version references it via `working_version`. No separate version manifest is needed.

### Secrets

Entries flagged `secret: true` are sealed under the run's public key (supplied per call as
`?public_key=<base64>` in Approach 1, or read from `Team.export_public_key` in Approach 2; see
*Authorization*), and the command unseals them on insert. This covers the provider configs,
`documents.documentsource.config`, `bot_channels.experimentchannel.extra_data`, and
`experiments.participantdata.data` / `encryption_key`. See the [Secrets](#secrets) section for the
sealing mechanism.

## Files and content hydration

File *metadata* travels through the `files.file` slug like any other model. File *content* is
transferred separately, and chunk embeddings come over their own slug.

- **One `files` slug, both phases.** `files.file` is pulled in the structural phase (collection-backed
  files) and re-polled in the live phase (chat-attachment-backed files), so there's no separate
  attachment-files path. A `chat.chatattachment` row carries its backing files as a remapped `files`
  pk list (bare M2M); the importer applies it with `.set()` once the backing `File` rows have arrived
  via the live re-poll.
- **Content transfer (bulk zip), selected.** An admin dashboard action on the source lets the user
  download all of the team's files as one zip and upload it once to the target's storage bucket.
  Django stores only the storage key on each `File` row, not the bytes, so the records resolve to the
  uploaded objects, as long as the export preserves each file's original storage key and the upload
  keeps the same layout. No per-file API calls. Since the bytes arrive out of band, file content isn't
  covered by the `FKTranslation` parity check; the parity step verifies it separately (see *Parity
  check*).
- **Fallback (per-file API fetch).** For each `File` row, fetch the content bytes from the source via
  the existing Files API and store them against the created `File` record (the target assigns its own
  storage key). This is slower, but content parity then falls out of the FK-table check. Kept as a
  fallback for cases where preserving storage keys and layout via the zip isn't practical.
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

## Migration lock

A single per-team flag, `Team.is_migrating`, drives the whole migration; it's the "lock" the rest of
this document refers to. It's operational state, not migrated data, so it sits in `EXCLUDE_REGISTRY`
for `teams.team`: the command never copies the source value, and sets the flag explicitly on the
target instead (below).

**Arming it (source).** A team admin flips the flag from the source's team settings (gated by
`is_team_admin()`; the toggle is audited via `field_audit`). Arming is a deliberate, fast action at
the start of a migration. Syncing is quick, so the team is locked only briefly. While armed, on the
source:

1. **Structural changes are frozen.** The structural-creation paths (new chatbot, new service
   provider, pipeline edits, and so on) are blocked with a banner. Enforcement is centralized: a
   single guard utility (e.g. `team.assert_not_migrating()` or one decorator) is applied at every
   structural-creation path rather than each path re-checking the flag ad hoc, and a test pins down
   the set of paths it must cover, so a path can't silently drift out of coverage and let structural
   data change mid-migration. Live chat traffic is unaffected; the source stays fully usable for
   conversations.
2. **Scheduled-message firing stops.** `poll_scheduled_messages` is a global, unconditional beat
   task. The migration freeze is applied as a single named exclusion (e.g.
   `ScheduledMessage.objects.exclude_migrating()` or a shared `migrating_team_ids()` helper) consulted
   by both `poll_scheduled_messages` and timeout-trigger firing. `get_messages_to_fire()` itself is
   left unchanged, so the migration concern isn't baked into a general-purpose manager method that
   other callers would silently inherit. (`ScheduledMessage`'s path to its `team` is declared and
   documented for this filter.) The source stops firing scheduled messages for the team the moment the
   lock is armed.

**The same flag on the target.** When the command syncs the team (manifest entry #1) it sets
`is_migrating=True` on the target too. Because both firing paths consult the migrating-team
exclusion, the target holds its synced `ScheduledMessage` rows inert with no extra mechanism. The
creation-freeze guard is a view/API-layer check, so it never interferes with the command's own ORM
writes.

**No double-fire, no pile-up.** Source firing is frozen from the moment the lock is armed, and the
target stays inert until cutover clears its flag, so a given occurrence fires on exactly one server.
Since the sync is fast, the window in which neither server fires is short. The persisted live-data
cursors capture the source's final firing state, so a later rerun never repeats an already-sent
occurrence. `events.timeouttrigger` rows keep their source `config_changed_at`, so the
retroactive-firing gate (`TimeoutTrigger.timed_out_sessions()` filters messages `>= config_changed_at`)
carries the source's semantics rather than resetting to import time.

**Clearing it.** Cutover clears `is_migrating` on the target, so the target resumes firing from the
synced state (see *Cutover*). The source flag is left set, since the source team is abandoned after
cutover. A team admin can also clear the source flag manually to abort a migration before cutover,
which unfreezes structural changes and resumes source firing.

## Parity check

When the migration is complete, no `FKTranslation` row may have a null `target_key`: every synced
source row must have a created target row. A null entry means a resource was missed or a dependency
was never created, and the command can be re-run to fill the gaps.

**File content.** The selected bulk-zip transfer moves bytes out of band, so the `FKTranslation` check
doesn't cover file content. The parity step checks two things instead: an aggregate check, where the
object count and total byte size at the target bucket match the source for the team (this catches a
truncated or partial upload that a sample would miss); and a spot-check of a sample of files (e.g.
20–50 across the team), confirming each has content at its expected storage key. Loading every file's
bytes is unnecessary; the aggregate plus the sample confirm the upload landed intact. (Under the
per-file fallback, the FK-table check covers content already, since each row's bytes were fetched as
it was created.)

## Cutover

Cutover is the single, team-wide switch from the source to the target. Until this point the source is
still live, and the target, though fully built and kept current by repeated reruns of the command, is
held inert by the per-team migration lock. Cutover flips inbound traffic and outbound firing over to
the target in one controlled window. It's all-at-once for the team, not bot-by-bot: channels that
share a provider physically move together (below), so the team is the clean unit and the migration
lock stays per-team.

### Webhook re-registration by platform

Each channel's inbound webhook embeds the server's domain, so cutover re-points each platform at the
target. The unit of that flip differs by platform, which is why some are auto-flipped, some are
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

The dividing line is whether inbound is keyed by a per-bot credential (a Telegram token or Twilio
number, individually flippable, so the command automates them) or by one shared account/app/domain
(Meta, Slack, SureAdhere, where flipping it moves *every* bot on that provider at once). That shared
case is what the table marks **coupled**: the bots sharing a Meta app, Slack workspace, or SureAdhere
tenant all ride a single inbound registration, so they can't be moved one at a time. Flipping that one
registration switches all of them to the target at the same instant, so every bot on it has to cut
over together. Email and CommCare Connect are configured once at the deployment level and shared
across teams, so a single-team migration can't flip them in isolation; they're an out-of-band ops task
the command only reports.

### Cutover sequence

1. **Source firing is already frozen.** The migration lock (armed at the start; see *Migration lock*)
   already excludes the team from the source's `poll_scheduled_messages` and timeout-trigger firing,
   so the source's outbound timer was frozen *before* cutover began. That's what prevents the same
   message being sent from both servers. Inbound chat still works on the source at this point. Nothing
   to do here beyond confirming the lock is still armed.
2. **Flip the webhooks.** The command auto-flips Telegram and Twilio immediately, then prints two
   lists for the operator: **providers to update** (each Meta app, Slack workspace, or SureAdhere
   tenant, where flipping one moves all its bots) and **channels to update** (Turn.io, Web/widget
   embeds, API base-URL). Inbound moves to the target per channel as each flip lands. The target serves
   those conversations immediately, since the migration lock's firing freeze governs only
   *scheduled-message* firing, not inbound handling.
3. **Keep re-running through the window.** Until the last webhook is flipped, messages to
   not-yet-flipped channels still land on the source, so keep re-running the command to pull the new
   live data. The window in which some channels point at the target and others at the source is
   data-safe: source firing is frozen and each rerun pulls the stragglers.
4. **Final run once the source is quiet.** After the last channel is flipped (no new data can reach
   the source), run the command one last time so the target is fully caught up, including the frozen
   scheduled-message state.
5. **Clear the lock on the target.** Set `is_migrating=False` on the target team; its
   `poll_scheduled_messages` resumes from the synced state. Because source firing was frozen for the
   whole (short) migration window and the final run captured the source's last firing state, no
   scheduled occurrence is duplicated or skipped. The source team stays locked and is abandoned.

### Re-establishment checklist

Cutover also surfaces the resources that are re-established rather than migrated (see the
`KNOWN_EXCLUSIONS` set in the *Model classification* table), so nothing is silently dropped:

- **OAuth2 applications**: the team's `OAuth2Application` registrations to recreate on the target;
  external API clients re-consent (client secrets are hashed and tokens are bearer secrets, so neither
  is migrated).
- **Social login / MFA**: users relying on social login or MFA re-authenticate or re-enrol.
- **API keys**: hashed on the source, so re-issued on the target.
- **Slack installs**: re-added (channel re-registration is manual).
- **Email / CommCare Connect**: instance-level inbound routing, handled out of band by ops.

## Read API Reference

The full schema reference for the two source-side read endpoints the sync command consumes: the
manifest and the generic content-type slug endpoint. Per the timestamp rule, every row from a
`BaseModel`-derived model also carries `created_at`/`updated_at`; these are omitted from the schemas
below except where they're load-bearing (the live-data cursors).

### 1. `GET /api/v2/sync/manifest/`

Returns the ordered list of content types to pull, with the config each type needs. The caller goes
through the entries in order; there's no hardcoded order in the command. The manifest is also the
allowlist: the slug endpoint refuses any content type not listed here, so the generic endpoint can't
expose a model it shouldn't.

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

- `phase`: `structural` (synced once), `live` (synced as a delta on each run), or `structural+live`
  (the single `files.file` entry, synced in both passes).
- `cursor`: `pk` (filter `id > cursor`, for append-only/created-once rows) or `updated_at_id` (filter
  `(updated_at, id) > (cursor_ts, cursor_id)`, for mutable rows, so each rerun re-pulls rows that
  changed since the previous run).
- `secret`: when true, the model's `SECRET_REGISTRY` fields come back sealed to the run's public key
  (supplied per *Authorization*'s Approach 1 or 2). The flag is derived from `SECRET_REGISTRY`
  membership, not declared twice (a test asserts the two agree; see *guard tests*).
- `order_by`: overrides the default `id` ordering; `working_version_id_nulls_first` is used by
  `experiments.experiment` and `pipelines.pipeline` so every working version precedes any published
  version that references it.
- `through`: marks an explicit through model (extra columns) so the caller creates it via its own row
  rather than as a bare M2M id list.

No exclusion list is served; completeness is enforced by a test instead (see *guard tests*).

### 2. `GET /api/v2/sync/<app_label.model>/?cursor=<keyset>&limit=<n>[&public_key=<base64>]`

Resolves the content type and returns that model's team-scoped rows, paginated. A response holds rows
from that one model only, nothing nested. How a model is scoped to the team comes from
`TEAM_PATH_REGISTRY` (see *Serialization*): a model with its own `team` FK (anything extending
`BaseTeamModel`, plus `trace.trace`) filters on `team=team`; a model without one (`BaseModel` or plain
`Model`, e.g. `chat.chatmessage`, `pipelines.pipelinechatmessages`, `analysis.analysisquery`) filters
through a declared ORM lookup path to a parent that has a `team` (e.g. `chat__team`,
`chat_history__session__team`, `analysis__team`).

**Query params:** `cursor` (keyset value, omitted on the first page); `limit` (page size);
`public_key` (base64 DER, used by `secret: true` slugs under Approach 1; under Approach 2 the source
reads `Team.export_public_key` instead and this param is ignored; see *Authorization*).

**Response envelope:**

```jsonc
{
  "next_cursor": 5000,        // int for cursor=pk; { "updated_at": "...", "id": N } for cursor=updated_at_id
  "has_more": true,
  "results": [ /* one model's serialized rows */ ]
}
```

`next_cursor` is the last keyset value in the response; `has_more` indicates more rows beyond it.
Append-only/created-once slugs (`cursor: pk`) filter `id > cursor` ordered by `id`, which is immutable
and monotonic, so there's no boundary overlap. Mutable slugs (`cursor: updated_at_id`) filter
`(updated_at, id) > (cursor_ts, cursor_id)` ordered by the same composite, so a run of rows sharing one
`updated_at` is paged deterministically: never skipped (truncation) nor re-served forever (timestamp
ties). Upsert-by-source-pk keeps any re-served boundary row a no-op.

**Row shape** (produced by the generated `ModelSerializer`; see *Serialization*):

- The row's own primary key stays `id`.
- A foreign key serializes under its relation name with the source pk as the value (`consent_form`,
  not `consent_form_id`), DRF's default. The importer remaps these via `FKTranslation`.
- A bare M2M serializes to a pk list under its relation name, exactly the id list the importer feeds
  to `.set()`.
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

Each content type's rows are produced by a dynamically built DRF `ModelSerializer`, one per model
from a factory rather than dozens of hand-written classes, so a serializer can't drift from its model
and a new field is exported the moment it's added. The serializers are output-only; `.save()` is never
called. The factory leans on `ModelSerializer`'s defaults (own pk → `id`; FK → relation name + pk;
bare M2M → pk list; standard datetime/JSON/Decimal coercion) rather than fighting them.

Three registries, co-located with the manifest and maintained by code review, are the only per-model
surface:

- `EXCLUDE_REGISTRY`: model → fields to drop (`customuser.password`, `widget_version*` telemetry,
  soft-delete columns, excluded M2M). Passed as `Meta.exclude`. "Dump every field" is the default, and
  this is the small, explicit set of exceptions.
- `SECRET_REGISTRY`: model → field names that must be sealed in transit.
- `TEAM_PATH_REGISTRY`: model → the ORM lookup path the endpoint filters on to scope its queryset to
  one team, applied as `Model.objects.filter(<path>=team)` (the pattern existing views already use,
  e.g. `ChatMessage.objects.filter(chat__team=team)`). The default is `"team"`, the direct FK on every
  `BaseTeamModel` and on `trace.trace`, so only the slugs *without* a `team` FK need an entry:
  `chat.chatmessage` / `chat.chatattachment` → `chat__team`; `pipelines.pipelinechathistory` →
  `session__team`; `pipelines.pipelinechatmessages` → `chat_history__session__team`;
  `evaluations.evaluationrunaggregate` → `run__team`; `analysis.analysisquery` → `analysis__team`. The
  path is declared, never auto-derived: a nullable parent FK silently drops rows whose path is null
  (`trace.trace.team` is itself nullable by design), and some models have several candidate parents, so
  each path is a reviewed decision. `evaluations.evaluationmessage` is the one model with no single
  clean path (see *Open questions*).

A few models need a value that isn't a plain field dump: `teams.team.feature_flags` (flag names),
`teams.membership.groups` (group names), and the `is_global` flag on matched global rows. Each is a
`SerializerMethodField` passed to the factory as an extra field; these are the only genuinely
per-model code.

**Module layout:** `apps/api/v2/sync/serializers.py` holds the factory and `_SyncSecretMixin`;
`apps/api/v2/sync/manifest.py` holds the manifest entries, `SECRET_REGISTRY`, `EXCLUDE_REGISTRY`, and
`TEAM_PATH_REGISTRY`, a single maintenance surface for the whole endpoint.

### Secrets

Each provider's `config` is a JSON object stored encrypted at rest with the source environment's key.
Rather than singling out individual sensitive keys, the export treats the entire `config` object as
opaque: it's decrypted with the source key and re-encrypted (sealed) with the target's public key as
one blob. `documents.documentsource.config`, `bot_channels.experimentchannel.extra_data`, and
`experiments.participantdata.data` / `encryption_key` are handled the same way. This keeps the source
and target code agnostic to each provider type's field layout.

These secrets fall into two classes. *Encrypted at rest*: the five provider `config`s plus
`ParticipantData.data` / `encryption_key`, all detectable as `django_cryptography` `EncryptedMixin`
fields. *Plaintext at rest but sensitive by policy*: `documents.documentsource.config` and
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
plaintext-sensitive fields reach `to_representation` as plain Python values and take the same sealing
path. `public_key` rides in the serializer context from the endpoint's query param; non-secret models
never read it.

To transfer a secret field (the key handling differs by approach; see *Authorization*):

1. The run obtains an RSA keypair, generated ephemerally by the target (Approach 1) or created out of
   band and passed via `--private-key-path` (Approach 2).
2. The public key reaches the source either as `?public_key=<base64-DER>` on every `secret: true` slug
   call (Approach 1) or pre-registered as `Team.export_public_key` (Approach 2).
3. The source decrypts the field with its own env key and `seal`s it under that public key, returning
   the ciphertext as the field value.
4. The target unseals it with its private key and re-encrypts under its own env key on insert.

`seal` is envelope encryption: a random symmetric key encrypts the value, and the RSA public key wraps
that symmetric key. This is needed because a value can exceed RSA's direct size limit (e.g. the Vertex
AI service-account JSON). The exact envelope format is an implementation detail; to the caller the
field is an opaque base64 string. Plaintext secret values never appear in the response body. Under
Approach 1 the keypair is ephemeral (generated per run, discarded after import); under Approach 2 it's
operator-managed and longer-lived but never transmitted. Fields outside `SECRET_REGISTRY` are sent as
plaintext, including a provider's `name`/`type` and a messaging provider's `extra_data` (which holds
only derived values such as Meta's `verify_token_hash`).

### Authorization

The `/api/v2/sync/` endpoints (the manifest and the generic slug endpoint) authenticate with a
`UserAPIKey` whose owner is an admin of the team being migrated, i.e.
`request.team_membership.is_team_admin()` must hold (a superuser with temporary team access qualifies
via `SuperuserMembership`). The team is resolved *from the key* (`UserAPIKey.team`), so there's no
team URL parameter and a key can only ever reach its own team's data. A read-only key is enough, since
every endpoint is a `GET`. A non-admin key is rejected with 403.

These endpoints expose the full team's data including sealed secrets, so they must be tightly
authorized, strictly team-scoped, and audited. See `docs/agents/django_view_security.md`. Two further
guards:

- **Migration mode required.** The endpoints refuse to serve a team that isn't in migration mode
  (`is_migrating`), so data can only be pulled while the source is frozen and not firing scheduled
  messages (see *Migration lock*).
- **Manifest allowlist.** The generic slug endpoint rejects any content type not in the manifest, so
  it can't be coaxed into serving an unclassified model.

#### Secret-pull authorization: two approaches

The slug endpoint returns provider `config`, `ParticipantData.data`/`encryption_key`, and the other
`SECRET_REGISTRY` fields sealed to a public key, which the caller then unseals to plaintext. How that
key is handled sets the security posture, and there are two approaches.

**Approach 1: caller-supplied ephemeral key (baseline, flagged).** The target generates an ephemeral
RSA keypair per run and passes its public key as `?public_key=<base64>`; the source seals to whatever
key the caller sends. This is simple and self-service, but it carries a real risk worth flagging: a
stolen or compromised team-admin API key, used while migration is armed, is on its own enough to pull
and unseal every raw provider credential for the team, because the caller chooses the key the secrets
are sealed to, and in normal OCS those secrets are effectively write-only. Arming migration (an
audited, deliberate team-admin action) is the only gate. If this approach is used, every
secret-bearing response should be audited.

**Approach 2: pre-registered team key (extra security, preferred where secrets matter).** A new
`Team.export_public_key` field is registered by a team admin through an authenticated,
`field_audit`-logged action in the source's team settings, a deliberate second factor alongside the
API token. The source seals secrets to *that* registered key, read from the team; the `?public_key=`
query param is dropped. The management command doesn't generate a keypair; it takes
`--private-key-path` pointing at a keypair created out of band, so the private key never travels over
the wire and is never chosen by the caller. Consequences:

- A stolen API key alone is useless for secrets: an attacker also needs the private key, which never
  leaves the operator's machine, and an intercepted response is likewise useless.
- If `export_public_key` is unset, secret slugs fail closed (refuse); the endpoint never silently
  degrades to Approach 1.
- The keypair is long-lived (operator-managed) rather than per-run ephemeral: a private key now sits
  on the target host, but it's never transmitted.
- Registering the key is audited, the same as the `is_migrating` toggle.

### Guard tests

**Exhaustiveness guard.** A test enumerates `apps.get_models(include_auto_created=True)` (the
`include_auto_created` flag is required, since auto M2M through tables are otherwise invisible),
subtracts the manifest's slugs, and asserts the remainder equals a `KNOWN_EXCLUSIONS` set declared in
the test (each member commented with its reason). A new, unclassified model fails the test until it's
added either to the manifest or to `KNOWN_EXCLUSIONS`. This is the executable counterpart of the
*Model classification* table, modelled on
`apps/teams/tests/test_permissions.py::test_missing_content_types`.

**Secret tripwire.** Three checks guard against leaking secrets as plaintext:

- *Registry ⇄ manifest agreement.* A test asserts each manifest entry's `secret` flag equals
  `SECRET_REGISTRY` membership for that model, so the two can't drift.
- *Encrypted-at-rest coverage.* A test enumerates every `EncryptedMixin` field across the synced
  models and asserts each appears in `SECRET_REGISTRY`, so an encrypted-at-rest field can't be added
  without being sealed. A behavioural test then runs each synced model through its serializer and
  asserts every `SECRET_REGISTRY` field comes out as ciphertext, never plaintext (the library decrypts
  transparently on read, so naive serialization *would* leak).
- *Sensitive-plaintext baseline.* The plaintext-sensitive fields
  (`bot_channels.experimentchannel.extra_data`, `documents.documentsource.config`) can't be detected
  by type. A field-snapshot baseline over every synced model (not just the secret-carrying ones) trips
  CI whenever a field is added to any model the sync exports, forcing a classify-or-acknowledge
  decision (add to `SECRET_REGISTRY`/`EXCLUDE_REGISTRY`, or bump the baseline). Widening the baseline
  beyond the secret-carrying models closes the gap where a newly added sensitive field on an
  otherwise-non-secret synced model would auto-export with no tripwire.

### Testing strategy

The test pyramid keeps the bulk of coverage fast (plain unit tests) and reserves the DB for what
genuinely needs it:

- **Unit (no `django_db`).** The importer engine's transforms are pure data→data and are tested
  directly with in-memory fixtures: FK remap against a fake translation map, pipeline/node
  JSON-`params` rewrite (including the `action_id:operation_id` shape), generic-FK `app_label.model`
  resolution, M2M id-list translation, and a `seal`→unseal round-trip. The keyset-cursor comparison
  logic is extracted so it can be unit-tested without the DB.
- **Guard tests.** Exhaustiveness (model classification), the secret tripwire (registry⇄manifest
  agreement, encrypted-at-rest coverage, widened sensitive-plaintext baseline), and the `ResourceKind`
  mapped-or-skipped guard (see *Guard tests* and *Foreign keys and many-to-many*). Manifest order
  isn't guarded by a dedicated topological test; the representative-subset e2e plus the runtime parity
  check cover ordering.
- **Behavioural (factories + `django_db`).** Each synced model is run through its generated serializer
  to assert shape (FK as relation name + source pk, bare M2M as id list, `SECRET_REGISTRY` fields
  sealed, non-secret fields plaintext, timestamps present).
- **Composite-cursor edge cases.** A targeted `django_db` test constructs rows that share one
  `updated_at` across a page boundary and asserts they're paged without skipping or infinite
  re-serving.
- **Representative-subset e2e.** One model per *shape* (BaseTeamModel, no-team-path, versioned/
  working-first, through-model, bare-M2M, generic-FK, secret-bearing, JSON-ref pipeline/node), run
  against a mocked source: assert the target matches, a rerun is a no-op, and a mutated row re-syncs.
  Breadth comes from the per-model behavioural and exhaustiveness tests rather than a full-fleet e2e.
- **Authorization matrix.** A parametrized test covers non-admin → 403, cross-team isolation (a key
  can't reach another team's data), migration-mode gate, manifest allowlist, and (Approach 2) secret
  slugs failing closed when no `export_public_key` is registered.

*Prerequisite:* factory_boy factories must exist for the synced models; with ~62 models, some are
likely missing and are prerequisite test-infra work.

### Performance and source load

The migration moves a whole team's data through many calls, so two things matter: the command's own
throughput and the load it puts on the live source.

- **In-memory FK index.** `FKTranslation` is loaded into per-content-type `source_pk → target_pk`
  dicts and FKs are resolved in memory rather than with one SQLite read per FK per row; writes are
  persisted to SQLite. A content type's map can be evicted once nothing downstream references it. Even
  for large teams this is on the order of tens of MB (int→int per synced row).
- **Per-row writes by default.** Create, timestamp `.update()`, and translation record run per row.
  Batching (`bulk_create` plus bulk timestamp/translation) is held back as a later optimization for
  the few high-volume append-only tables (`chat.chatmessage`, `trace.trace`,
  `pipelines.pipelinechatmessages`, `files.filechunkembedding`), and only if profiling shows it's
  needed; it complicates the identity logic, so it isn't paid for speculatively.
- **Embedding page size.** `files.filechunkembedding` carries a full vector per row, so its slug uses
  a smaller page `limit` than lightweight tables.
- **Source-side indexes (live server).** Each mutable slug sorts and filters on `(updated_at, id)`, and
  the no-team-FK slugs join deep paths (e.g. `pipelines.pipelinechatmessages →
  chat_history__session__team`). The indexes those queries depend on (composite `(updated_at, id)` per
  mutable slug, and indexed team-path joins) are enumerated and ensured, so a large-team migration
  never runs unindexed scans or sorts against production.
- **Rate limiting.** The sync endpoints are rate-limited on the source, and the command exposes a
  configurable page size and an optional inter-request delay, so a heavy migration can be run gently
  (or off-peak) without spiking load on the live source.

### Model classification

Every registered model is accounted for below: either **synced** (a manifest entry) or **excluded**
(with a reason that lives in the test's `KNOWN_EXCLUSIONS`). The synced rows are numbered 1–62 in call
order; the excluded list continues 63–134, one continuous run over every registered model
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
run is gapless: 1–134 covers all 134 registered models exactly once.

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
  - (`waffle.flag` is **not** registered; OCS swaps in `teams.flag` (#111) as the flag model)
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
  outright, since group/permission membership is re-established on the target:
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

- **No unbounded payload:** everything is paginated.
- **Coverage and testability:** the guard test maps one-to-one to the manifest, and nothing is hidden
  inside a bundle.
- **Simple to follow and audit:** one call, one model.
- **One `files` slug** polled in both phases, with no files/attachment-files split.
- **Server owns the order** (the manifest), so the command is generic and a newer source can add
  models without a command change.
- **The manifest is the allowlist,** which removes the risk of the generic endpoint exposing too much.

The costs accepted in exchange:

- **New pattern:** v2 today is router + ViewSets; a content-type dispatch view with per-type
  cursor/secret config and strict team-scoping is new to the codebase.
- **More round-trips:** many ordered, paginated calls.
- **No atomic bundles:** a chatbot, its channels, and its events arrive in separate calls, so a
  half-applied chatbot is a transient state between calls (checkpoint/resume covers it).
- **The manifest declares only what and in what order;** the caller still owns the *how* (FK remap,
  secrets, through handling). That's fine while the flag set stays small, but it's a warning sign if
  every model needs a special flag.

## Addendum

### FK Translation Table

```python
class ForeignKeyTranslation():
    content_type: chat
    source_key: int
    target_key: int[nullable]
```

### Checkpointing

The `ForeignKeyTranslation` table acts as a checkpoint table. An empty `target_key` means we haven't
synced that resource yet. We should be able to rerun everything to continue.

For content types marked **`live`**, we don't need to persist a separate cursor; the checkpoint falls
out of the rows already synced. On a rerun, take the latest timestamp (`created_at` and/or
`updated_at`, depending on the slug's `cursor` rule) among the rows already imported for that content
type and use it directly as the cursor for the next request. That timestamp marks exactly how far the
previous run got, so the next page resumes where the last one left off, with no extra bookkeeping. The
same idea applies to append-only `pk` slugs: the largest synced `id` is the cursor for the next pull.

## Questions

### Resolved

- **Keeping the API current as models are added:** a guard test enumerates
  `apps.get_models(include_auto_created=True)`, subtracts the manifest's slugs, and asserts the
  remainder equals `KNOWN_EXCLUSIONS`; serialization is a field-introspection-driven `ModelSerializer`
  factory so new fields ride along; a secret tripwire (registry⇄manifest agreement, encrypted-at-rest
  coverage, sensitive-plaintext baseline) guards secrets. See *Guard tests*.
- **Versioning:** `experiments.experiment` and `pipelines.pipeline` are pulled
  `order_by working_version_id NULLS FIRST, id`, so every working version precedes any published
  version that references it via `working_version`.
- **Scheduled messages:** synced as live data (`events.scheduledmessage`); the per-team migration lock
  stops the source firing and holds the target's synced rows inert, so neither server double-fires
  during the migration window. See *Migration lock*.
- **Migration lock, enabling and scope:** a single per-team flag (`Team.is_migrating`), armed by a
  team admin from the source's team settings (not auto-enabled on the first API call). While armed it
  freezes the main structural-creation paths on the source *and* stops the source firing scheduled
  messages; the same flag, set by the command on the target, holds the target's synced messages inert
  until cutover. The sync endpoints require both a team-admin API key and the team to be in migration
  mode. See *Migration lock* and *Authorization*.
- **Paginating live data:** per-slug keyset pagination set by the manifest `cursor` field (`pk` for
  append-only, `updated_at_id` for mutable), consumed in manifest order, with each slug's cursor
  persisted locally between runs. See the slug endpoint reference.
- **Re-syncing live data:** the command is single-pass. Each run ensures the structural data is present
  (idempotent), then syncs the live data once as a delta from the persisted cursors, then exits. There
  is no internal polling loop; to take another iteration the operator reruns the command. See *The sync
  process*.
- **OAuth models:** deferred, re-established on the target via a cutover checklist (same bucket as
  Slack/webhook re-registration), since no synced model depends on them and there is no OAuth
  auth-provider type. See *Cutover* and `KNOWN_EXCLUSIONS`.
- **Chatbot switchover:** a single team-wide cutover, not bot-by-bot. Auto-flip what we can
  (Telegram/Twilio), report the provider-level flips (Meta/Slack/SureAdhere) and per-channel manual
  flips (Turn.io/Web/API), and treat email and CommCare Connect as instance-level ops. The firing gate
  stays per-team. See *Cutover*.

### Open

- **Team-scoping `evaluations.evaluationmessage`:** unlike every other live model it has no single
  non-null path to a team. `session`, `input_chat_message`, and `expected_output_chat_message` are all
  nullable (null for CSV-imported or manually-created rows), so a single `TEAM_PATH_REGISTRY` lookup
  like `session__team` would silently drop those rows. Two options:
  1. **Disjunction:** filter on `Q(session__team=team) | Q(input_chat_message__chat__team=team)`,
     accepting that rows where *all* parents are null still can't be reached through a path (they'd
     need a different handle, e.g. the `evaluationdataset.messages` M2M, or be excluded).
  2. **Exclude:** drop `evaluationmessage` from the manifest into `KNOWN_EXCLUSIONS` for now, treating
     evaluation messages as out of scope until a clean scoping rule is decided.

  `TEAM_PATH_REGISTRY` would need to allow a `Q`-expression (not just a string path) for option 1.
