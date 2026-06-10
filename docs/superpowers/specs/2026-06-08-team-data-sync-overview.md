---
status: active
---

# Team Data Sync — Overview

## Glossary

- **Source Server**: The server being migrated from.
- **Target Server**: The server being migrated to.

## Overview

The migration process runs as a management command on the target server. This command fetches data
from the source server over HTTPS and recreates it locally using the ORM.

The following APIs will be used (those marked `*` are new, served under the `/api/v2/sync/` prefix):

- `*` **Export team** — `GET /api/v2/sync/export-team/`: Returns all team data plus the "building
  block" resources that chatbots are composed of (consent forms, source material, collections,
  custom actions, etc.), and all chatbot **and** pipeline versions for the team, each grouped by
  working version.
- **[Existing] Files API**: Fetches a file's content (bytes) given its metadata.
- `*` **File chunk embeddings** — `GET /api/v2/sync/file-chunk-embeddings/`: A primary-key-paginated
  bulk export of the team's chunk embeddings (text + vector), so the target need not re-run the
  embedding model.
- `*` **Chatbot export** — `GET /api/v2/sync/chatbots/{id}/export/`: Fetch a single chatbot version
  (working or published) by its integer pk, including its FK references, channels, and events.
- **[Existing] Pipeline read** — `GET /api/v2/pipelines/{id}/`: Fetch a pipeline's raw graph
  (nodes + edges with full params).
- `*` **Living data** — `GET /api/v2/sync/living-data/`: A cursor-paginated delta stream of the
  data that grows with chatbot interactions.

The migration runs in six steps: (1) general resources, (2) file hydration, (3) pipeline creation,
(4) chatbot and channel creation, (5) live data, (6) parity check.

## Setup

Running the command creates a local SQLite DB named for the team slug and initialises the
`FKTranslation` table. No chatbots, users, service providers, or other resources may be created on
the **source** while the migration runs.

**FK translation rule.** Every source row we sync gets an `FKTranslation` row keyed by
`(content_type, source_key)`, with `target_key` left null until the row exists on the target. Every
`*_id` field in every response is resolved through this table; creating a row fills in its
`target_key`. The table doubles as the checkpoint (Step 6) and lets the command be re-run to resume.

**Timestamp preservation rule.** Every record returned by these APIs carries the source row's
`created_at` and `updated_at` values (for every model with those fields — i.e. anything extending
`BaseModel`, which defines them as `auto_now_add`/`auto_now`). Because those Django field options
ignore any value supplied on insert/save, the ORM cannot set them directly. So immediately after
creating (or upserting) each row, the command issues a raw SQL `UPDATE` to write the source
`created_at`/`updated_at` back onto the new row. This applies to every resource in every step below
and keeps the target's timestamps faithful to the source. Example fields are shown on a couple of
rows in each response below; they are present on **all** records, omitted elsewhere for brevity.

The full endpoint schemas are inlined in the **Read API Reference** section below. The per-step
sections that follow cover, for each step, the call we make, the response shape, how we use it, and
how it feeds the FK table.

## Step 1 - General resources

**Call.** `GET /api/v2/sync/export-team/?public_key=<base64>`. The target generates an ephemeral RSA
keypair per run; the source re-encrypts every provider `config` (and other secret blobs) under the
public key (see the [Secrets](#secrets) section below).

**Not exported:**

- **Assistants** and **MCP servers** — excluded by decision; a referencing pipeline/chatbot node
  keeps a dangling FK (handled out of band).
- **Audit logs** — not needed.
- **Evaluations** and **human-annotation** review data — future scope. (Tags and tagged items *are*
  synced — in this step and Step 5.)
- OAuth/social login, hashed API keys, and Slack installs are re-established or re-registered on the
  target, not migrated. See the Model → source-API mapping table in the Read API Reference section
below for the exhaustive list.

**Response** (abbreviated):

```json
{
  "team": { "name": "Acme Corp", "slug": "acme", "feature_flags": ["flag_a", "flag_b"], "created_at": "2024-01-15T09:30:00Z", "updated_at": "2024-03-02T11:00:00Z" },
  "users": [
    { "id": 1, "email": "alice@acme.com", "username": "alice@acme.com", "first_name": "Alice", "last_name": "B.", "created_at": "2024-01-15T09:31:00Z", "updated_at": "2024-02-20T14:05:00Z" }
  ],
  "memberships": [
    { "user_id": 1, "role": "admin", "groups": ["Team Admins"] }
  ],
  "service_providers": {
    "llm_providers": [
      { "id": 1, "name": "OpenAI Prod", "type": "openai", "config": "<encrypted>", "created_at": "2024-01-16T08:00:00Z", "updated_at": "2024-01-16T08:00:00Z" }
    ],
    "voice_providers": [],
    "trace_providers": [],
    "messaging_providers": [],
    "auth_providers": []
  },
  "llm_provider_models": [
    { "id": 1, "type": "openai", "name": "gpt-4o", "max_token_limit": 128000, "is_global": false }
  ],
  "embedding_provider_models": [
    { "id": 2, "type": "openai", "name": "text-embedding-3-small", "is_global": true }
  ],
  "synthetic_voices": [
    { "id": 1, "name": "Rachel", "voice_provider_id": 3, "external_id": "21m00", "is_global": false }
  ],
  "custom_actions": [],
  "consent_forms": [],
  "source_materials": [],
  "surveys": [],
  "tags": [
    { "id": 8, "name": "support" }
  ],
  "collections": [
    {
      "id": 10,
      "name": "Support Docs",
      "embedding_provider_model_id": 2,
      "files": [],
      "document_sources": []
    }
  ],
  "notification_event_types": [],
  "user_notification_preferences": [],
  "experiment_versions": { "20": [23, 24], "22": [25] },
  "pipeline_versions": { "40": [43, 44], "42": [45] }
}
```

**Use.** Recreate each resource on the target and record its `target_key`. Enable the team's
`feature_flags` as part of team setup: for each flag name, add the new team to the matching Waffle
`Flag`'s `teams` M2M. The flags themselves are global (defined in code, names prefixed `flag_`), so
they are matched by name like other global rows and never recreated — a name with no matching target
flag is an error. Global rows (`is_global: true` models/voices) are **not** recreated — match each
to the target's already-seeded row by natural key and record only the mapping; no match is an error.
`experiment_versions` and
`pipeline_versions` are the manifests for Steps 3 and 4: each maps a family's working-version pk to
its other version pks, so those steps create the working version first. After creating each row,
write its source `created_at`/`updated_at` back with raw SQL (timestamp rule).

## Step 2 - File hydration

File content is transferred by one of two methods; chunk embeddings are always pulled over the API.

**File content — option A (per-file API fetch).** For each `collections[].files[]` entry, fetch the
content bytes from the source via the existing Files API and store them against the `File` row
created in Step 1. The target assigns its own storage key.

**File content — option B (bulk zip).** An admin dashboard action on the source lets the user
download all of the team's files as one zip and upload it once to the target's storage bucket. Django
stores only the storage key on each `File` row, not the bytes, so the `File` records created in Step
1 resolve to the uploaded objects — provided the export preserves each file's original storage key
and the upload keeps the same layout. No per-file API calls.

**Embeddings.** `GET /api/v2/sync/file-chunk-embeddings/?cursor=<id>&limit=<n>` — paginated by
primary key (`id > cursor`), each row carrying chunk text + vector.

**Response** (embeddings):

```json
{
  "next_cursor": 5000,
  "has_more": true,
  "file_chunk_embeddings": [
    {
      "id": 100,
      "file_id": 11,
      "collection_id": 10,
      "chunk_number": 0,
      "text": "Our refund policy ...",
      "embedding": [0.0123, -0.0456, 0.0789],
      "created_at": "2024-02-01T10:00:00Z",
      "updated_at": "2024-02-01T10:00:00Z"
    }
  ]
}
```

**Use.** Insert each embedding, remapping `file_id` and `collection_id` through the FK table;
transferring embeddings as data lets the target skip re-running the embedding model. This step runs
after Step 1 because file content, embeddings, and their FK remaps all reference rows created there.

## Step 3 - Pipeline creation

**Call.** `GET /api/v2/pipelines/{id}/` for every pipeline version in `pipeline_versions` — the
working version (group key) first, then its other versions. One endpoint serves both
chatbot-attached and standalone pipelines.

**Response** (abbreviated): the round-trippable react-flow graph — `nodes[]` (each with
`data.params`) and `edges[]`. `working_version_id` is null for the working version, else the family
working pk.

**Use.** Remap the FK refs in `data.params` (`llm_provider_id`, `llm_provider_model_id`,
`source_material_id`, custom-action operation refs, ...) through the FK table, then create the
pipeline and record its `target_key`, then write its source `created_at`/`updated_at` back with raw
SQL (timestamp rule). Non-working versions set `working_version_id` from the working version created
first. Refs to excluded assistant/MCP nodes stay dangling.

## Step 4 - Chatbot and channel creation

**Call.** `GET /api/v2/sync/chatbots/{id}/export/?public_key=<base64>` for each version in
`experiment_versions` — working version first, then the others.

**Response** (abbreviated):

```json
{
  "pk": 20,
  "working_version_id": null,
  "name": "Customer Support Bot",
  "created_at": "2024-02-10T12:00:00Z",
  "updated_at": "2024-03-05T16:30:00Z",
  "settings": {
    "seed_message": null,
    "conversational_consent_enabled": false,
    "voice_response_behaviour": "reciprocal",
    "file_uploads_enabled": false,
    "participant_allowlist": []
  },
  "consent_form_id": 3,
  "pre_survey_id": null,
  "post_survey_id": 9,
  "voice_provider_id": 4,
  "synthetic_voice_id": 6,
  "trace_provider_id": null,
  "pipeline_id": 42,
  "channels": [
    {
      "id": 42,
      "name": "Support TG",
      "platform": "telegram",
      "messaging_provider_id": 6,
      "extra_data": "<encrypted>",
      "created_at": "2024-02-10T12:05:00Z",
      "updated_at": "2024-02-10T12:05:00Z"
    }
  ],
  "events": {
    "static_triggers": [
      {
        "id": 11,
        "type": "conversation_end",
        "is_active": true,
        "action": { "id": 100, "type": "pipeline_start", "params": { "pipeline_id": 42 } }
      }
    ],
    "timeout_triggers": []
  }
}
```

**Use.** Resolve every `*_id` (including `pipeline_id`, pointing at a Step-3 pipeline) through the FK
table, then create the chatbot version and record its `target_key`. Non-working versions set
`working_version_id` from the working version. Channels and events are embedded — recreate each
channel (its `extra_data` decrypted and re-encrypted under the target key) and each trigger/action.
Record each `EventAction` id in the FK table so Step 5's scheduled messages can resolve it. Write
the source `created_at`/`updated_at` back with raw SQL on the chatbot, channel, and event rows
(timestamp rule). Channel webhook re-registration with the external platform stays a manual step.

## Step 5 - Live data

Live data is the tables that grow with chatbot interactions: participants, sessions, chat messages,
traces, participant data, and tagged items.

**Call.** `GET /api/v2/sync/living-data/?cursor=<iso_timestamp>&limit=<n>`, polled repeatedly,
passing `next_cursor` each time until cutover.

**Response** (abbreviated): arrays of `participants`, `sessions`, `chat_messages`, `traces`,
`participant_data`, `custom_tagged_items`, plus `next_cursor` and `has_more`. Each row carries its
source `created_at`/`updated_at`.

**Use.** Upsert each row by source pk (idempotent, so the boundary overlap from the `>=` cursor is a
no-op) and record its `target_key`; write each row's source `created_at`/`updated_at` back with raw
SQL after the upsert (timestamp rule). Apply `participants` **before** the other arrays in a batch —
sessions, participant data, and tagged items reference them. Keep paginating while `has_more` is
true; otherwise record `next_cursor` as the checkpoint and wait before the next poll.

## Step 6 - Parity check

When the migration is complete, no `FKTranslation` row may have a null `target_key` — every synced
source row must have a created target row. A null entry means a resource was missed or a dependency
was never created; the command can be re-run to fill the gaps.

**File content.** Under option A, the FK-table check covers files implicitly — each row's bytes were
fetched and stored as it was created. Under option B the bytes arrive out of band, so the check
instead spot-checks a sample of files (e.g. 20–50 across the team) and confirms each has content at
its expected storage key. Loading every file's bytes is unnecessary; a sample confirms the upload
landed.

## Read API Reference

Steps 1–6 describe the migration flow with abbreviated responses. This section is the full schema
reference for the five source-side read endpoints the sync command consumes — four new ones under
`/api/v2/sync/` plus the existing v2 pipeline read. Per the timestamp rule, every row additionally
carries `created_at`/`updated_at`; these are omitted from the schemas below except where they are
load-bearing (the living-data cursors).

### 1. `GET /api/v2/sync/export-team/?public_key=<base64>`

Returns a complete structural snapshot of the team. The `public_key` query parameter is the
target's RSA public key — the source uses it to re-encrypt provider secrets before sending (see
[Secrets](#secrets)).

**Response:**

```json
{
  "team": {
    "name": "Acme Corp",
    "slug": "acme",
    "feature_flags": ["flag_a", "flag_b"]
  },
  "users": [
    { "id": 1, "email": "alice@acme.com", "username": "alice@acme.com",
      "first_name": "Alice", "last_name": "B." }
  ],
  "memberships": [
    { "user_id": 1, "role": "admin", "groups": ["Team Admins"] }
  ],
  "service_providers": {
    "llm_providers": [
      { "id": 1, "name": "OpenAI Prod", "type": "openai",
        "config": "<entire config object, encrypted with public_key>" }
    ],
    "voice_providers": [
      { "id": 3, "name": "ElevenLabs", "type": "elevenlabs",
        "config": "<encrypted with public_key>" }
    ],
    "trace_providers": [
      { "id": 4, "name": "Langfuse", "type": "langfuse",
        "config": "<encrypted with public_key>" }
    ],
    "messaging_providers": [
      { "id": 5, "name": "Twilio Prod", "type": "twilio",
        "config": "<encrypted with public_key>", "extra_data": {} }
    ],
    "auth_providers": [
      { "id": 6, "name": "CRM Bearer", "type": "bearer",
        "config": "<encrypted with public_key>" }
    ],
    "llm_provider_models": [
      { "id": 1, "type": "openai", "name": "gpt-4o", "max_token_limit": 128000, "is_global": false },
      { "id": 5, "type": "openai", "name": "gpt-4o-mini", "max_token_limit": 128000, "is_global": true }
    ],
    "embedding_provider_models": [
      { "id": 2, "type": "openai", "name": "text-embedding-3-small", "is_global": true }
    ]
  },
  "synthetic_voices": [
    { "id": 1, "name": "Rachel", "service": "ElevenLabs", "voice_provider_id": 3,
      "language": "English", "language_code": "en", "neural": true, "external_id": "21m00...",
      "is_global": false },
    { "id": 2, "name": "Joanna", "service": "AWS", "voice_provider_id": null,
      "language": "English", "language_code": "en", "neural": true, "external_id": "Joanna",
      "is_global": true }
  ],
  "custom_actions": [
    { "id": 1, "name": "Lookup CRM", "description": "...", "prompt": "...",
      "server_url": "https://api.example.com", "api_schema": {},
      "auth_provider_id": 6, "allowed_operations": ["getCustomer"] }
  ],
  "consent_forms": [
    { "id": 3, "name": "Default Consent", "is_default": true,
      "consent_text": "...", "capture_identifier": true, "identifier_label": "Email",
      "identifier_type": "email", "confirmation_text": "..." }
  ],
  "source_materials": [
    { "id": 6, "topic": "FAQ", "description": "...", "material": "..." }
  ],
  "surveys": [
    { "id": 7, "name": "Post-chat Survey", "url": "https://...", "confirmation_text": "..." }
  ],
  "tags": [
    { "id": 8, "name": "support" },
    { "id": 9, "name": "vip" }
  ],
  "collections": [
    {
      "id": 10, "name": "Support Docs", "is_index": true, "is_remote_index": false,
      "embedding_provider_model_id": 2, "llm_provider_id": 1,
      "files": [
        { "id": 11, "name": "policy.pdf", "content_type": "application/pdf",
          "content_size": 12345, "summary": "...",
          "collection_file": { "status": "completed", "external_id": "", "metadata": {} } }
      ],
      "document_sources": [
        { "id": 30, "source_type": "github",
          "config": "<encrypted with public_key>",
          "auto_sync_enabled": true, "auth_provider_id": 6 }
      ]
    }
  ],
  "notification_event_types": [
    { "id": 1, "identifier": "pipeline_failed", "level": 30, "event_data": {} }
  ],
  "user_notification_preferences": [
    { "user_id": 1, "in_app_enabled": true, "in_app_level": 20,
      "email_enabled": false, "email_level": 30, "do_not_disturb_until": null }
  ],
  "experiment_versions": {
    "20": [23, 24],
    "21": [],
    "22": [25]
  },
  "pipeline_versions": {
    "40": [43, 44],
    "41": [],
    "42": [45]
  }
}
```

**Notes:**

- `experiment_versions` maps each working-version pk (the JSON key) to the list of its family's
  other (published/numbered) version pks; a family with no published versions has an empty list.
  Together with `pipeline_versions` (the same shape, for pipelines) these are the import manifest and
  the `FKTranslation` source keys. For
  each family the importer first fetches and creates the **working version** (the map key), then each
  version in its list — every chatbot version is fetched via the integer-keyed export (endpoint 2,
  `GET /api/v2/sync/chatbots/{id}/export/`). Creating the working version first lets the non-working
  versions populate their `working_version_id` FK. It then applies `pipeline_versions` the same way —
  create each family's working pipeline first, then its other versions — fetching every pipeline
  version (chatbot-attached and standalone) by integer id via endpoint 3.
- `llm_provider_models` and `embedding_provider_models` include **both team-scoped and global
  (null-team) rows**, each flagged with an `is_global` boolean. Global rows are not carried to be
  recreated — they are already seeded on the target — but so the importer can resolve FK references
  to them locally: it matches each global row's natural key (`type` + `name`, plus `max_token_limit`
  for LLM models) against the target's existing global row and records the mapping in
  `FKTranslation`. A global row with no match on the target is an import error.
- `synthetic_voices` includes **both team-scoped and global voices**, each flagged with `is_global`
  (team-scoped voices are tied to the team's own voice providers; global/system voices have a null
  `voice_provider_id`). As with the model lists, global voices are not recreated — they ride along
  so the importer can match each to its already-seeded target row (by `external_id` + `service`, or
  the legacy metadata tuple when `external_id` is null) and record the `FKTranslation`. No match is
  an import error.
- `memberships` carry their role and group names; the target maps them to its own (seeded)
  `auth.Group` rows by name.
- Assistants, MCP servers, and tool resources are **not** exported. A pipeline/chatbot that
  references an assistant or MCP node will have an unresolvable FK on import — a known limitation,
  handled out of band.
- File *content* is not included. The importer creates an empty `File` record from each file's
  metadata, then fetches the bytes via the existing file-content API and stores them in target
  object storage. Chunk embeddings are excluded here too and pulled in bulk via endpoint 5, so the
  target need not re-run the embedding model.

### 2. `GET /api/v2/sync/chatbots/{id}/export/?public_key=<base64>`

A dedicated sync export of a single chatbot **version** — working or published — the round-trippable
counterpart to the digested `/inspect/` projection. It carries everything needed to recreate that
version: its settings and FK refs, its `ExperimentChannel` rows with full (re-encrypted) config, and
its events with raw action params. Its pipeline is referenced by `pipeline_id` and fetched separately
via endpoint 3. Addressed by the integer experiment `pk` (matching a pk in
`export-team.experiment_versions` — either a map key or a list entry), not `public_id` — this is a
sync endpoint, not bound by the v2 UUID convention. The `public_key` query parameter is the target's
RSA public key, used to re-encrypt the channels' secret-bearing `extra_data` (see [Secrets](#secrets)).

**Response:**

```jsonc
{
  "pk": 20,                       // numeric primary key — how the endpoint is addressed; recorded in FKTranslation
  "public_id": "5a3c…",           // UUID — carried for reference only
  "working_version_id": null,     // pk of the family's working version; null when this IS it
  "name": "Customer Support Bot",
  "description": "…",
  "version_number": 0,
  "is_working_version": true,
  "is_default_version": false,
  "version_description": null,
  "team_slug": "acme",
  "published_version": { "id": "asdf", "published_on": "...", "comment": "..." },
  "settings": {                   // non-secret Experiment fields, null if unset
    "seed_message": null,
    "conversational_consent_enabled": false,
    "voice_response_behaviour": "reciprocal",
    "echo_transcript": false,
    "debug_mode_enabled": false,
    "file_uploads_enabled": false,
    "participant_allowlist": []
  },
  "consent_form_id": 3,
  "pre_survey_id": null,
  "post_survey_id": 9,
  "voice_provider_id": 4,
  "synthetic_voice_id": 6,
  "trace_provider_id": null,
  "pipeline_id": 42,
  "channels": [                   // full config — recreates each ExperimentChannel (moved here from export-team)
    { "id": 42, "name": "Support TG", "platform": "telegram",
      "external_id": "9f3a-…", "messaging_provider_id": 6,
      "extra_data": "<encrypted with public_key>" }
  ],
  "events": {
    "static_triggers": [
      { "id": 11, "type": "conversation_end", "is_active": true,
        "action": { "id": 100, "type": "pipeline_start", "params": { "pipeline_id": 42 } } }
    ],
    "timeout_triggers": [
      { "id": 22, "delay_seconds": 86400, "total_num_triggers": 1,
        "trigger_from_first_message": false, "is_active": true,
        "config_changed_at": "2026-05-01T08:00:00Z",
        "action": { "id": 101, "type": "send_message_to_bot", "params": {} } }
    ]
  }
}
```

**Notes:**

- Addressed by the integer `pk` — the same values `export-team.experiment_versions` lists (as map
  keys and list entries) and that living-data rows reference — so no UUID↔pk bridging is needed.
  `public_id` rides along for reference (e.g. target-side deep links) but is not used for FK
  resolution.
- `working_version_id` is the numeric pk of the family's working version, mirroring the
  `Experiment.working_version` self-FK. It is `null` for the working version itself and set on every
  published/numbered version. Because the importer creates each family's working version first, this
  field is always resolvable via `FKTranslation` when the other versions are inserted.
- `settings` holds the non-secret `Experiment` fields only. (`use_processor_bot_voice` is **not**
  included — the field was removed in migration `0142`.)
- The `*_id` fields are source-env numeric FK ids the sync engine remaps via `FKTranslation`. A
  `pipeline_id` of `null` means the chatbot has no pipeline; otherwise it resolves via `FKTranslation`
  to the pipeline version created from `export-team.pipeline_versions` (endpoint 3).
- `channels[]` carries each `ExperimentChannel` in full, including its secret-bearing `extra_data`
  re-encrypted with `public_key` (see [Secrets](#secrets)). The experiment is implicit — these rows
  attach to the chatbot being created, so no `experiment_id` is sent. Soft-deleted channels
  (`deleted=true`) and telemetry fields (`widget_version*`) are excluded. Recreating the rows is in
  scope, but **registering** each channel's webhook with the external platform remains a
  manual/operational step on the target (see Step 4).
- `events.*.action` carries its own `id` — the `EventAction` pk, recorded in `FKTranslation` so that
  living-data rows referencing it (`ScheduledMessage.action`, for system-created schedules) can be
  remapped. `params` are emitted **raw** — not the digested `/inspect/` shape, which strips
  `pipeline_id` and collapses `schedule_trigger` cadence — and may embed FK refs the engine remaps.
- `timeout_triggers[].config_changed_at` is preserved so the trigger's retroactive-firing gate
  (`TimeoutTrigger.timed_out_sessions()` filters reference messages `>= config_changed_at`) carries
  the source's semantics rather than resetting to import time.
- Archived triggers (`is_archived=true`) are excluded.

### 3. `GET /api/v2/pipelines/{id}/`

Returns the raw pipeline graph (nodes + edges with full params) for a given pipeline id
(numeric — pipelines have no public UUID). This is the same round-trippable react-flow shape the
write API spec uses for `GET /api/v2/chatbots/{id}/pipeline/` (`apps/pipelines/flow.py`), **not**
the digested `/inspect/` projection.

This is the **sole** pipeline read: the importer fetches every pipeline **version** through it. Like
chatbots, `export-team.pipeline_versions` groups every pipeline family by working version
(`working_version_pk → [other version pks]`) and covers both a chatbot's own pipeline (referenced by
the `pipeline_id` in its chatbot export) and **standalone** pipelines attached to no chatbot. The
importer creates each family's working version first, then its other versions with their
`working_version_id` FK pointed at it. Standalone pipelines are a first-class concept (created via
the pipelines UI with `experiment=None`) and can be targeted by a `pipeline_start` trigger on any
chatbot, so they must be fetchable independently of any chatbot.

**Response:**

```jsonc
{
  "id": 42,
  "name": "Support Flow",
  "version_number": 0,
  "working_version_id": null,            // pk of the family's working version; null when this IS it
  "nodes": [
    {
      "id": "llm-1",                       // flow_id (react-flow node id)
      "type": "pipelineNode",              // "pipelineNode" | "startNode" | "endNode"
      "position": { "x": 300, "y": 0 },
      "data": {
        "id": "llm-1",
        "type": "LLMResponseWithPrompt",   // node class name
        "label": "LLM",
        "params": {
          "llm_provider_id": 1, "llm_provider_model_id": 5,
          "source_material_id": 6, "custom_actions": ["1:getCustomer"]
        }
      }
    }
  ],
  "edges": [
    { "id": "edge-1", "source": "start-1", "target": "llm-1",
      "sourceHandle": "output", "targetHandle": "input" }
  ]
}
```

Node `data.params` contain soft-JSON FK refs (`llm_provider_id`, `llm_provider_model_id`,
`source_material_id`, `custom_actions` operation refs, etc.) that the sync engine remaps. Refs to an
assistant or MCP node remain dangling (those resources are excluded by decision — see endpoint 1
notes). No `ETag` is returned for this read; the write spec's chatbot-scoped variant carries one for
optimistic concurrency, which the sync read does not need.

### 4. `GET /api/v2/sync/living-data/?cursor=<iso_timestamp>&limit=<n>`

Returns all living-data rows created or updated since `cursor`. Called repeatedly until cutover;
the sync engine stores `next_cursor` and passes it on the next call.

**Cursor semantics:**

| Resource | Filter |
| --- | --- |
| `chat_messages`, `traces`, `custom_tagged_items` | `created_at >= cursor` (append-only) |
| `participants`, `sessions`, `participant_data` | `created_at >= cursor OR updated_at >= cursor` |

`next_cursor` is the maximum `created_at`/`updated_at` observed across all rows in the response.
Because `>=` is used at the boundary, the first row of the next page may overlap with the last row
of the current page; the sync engine's upsert-by-source-pk treats duplicates as no-ops.

**Response:**

```json
{
  "cursor": "2026-06-09T10:00:00Z",
  "next_cursor": "2026-06-09T14:32:00Z",
  "has_more": true,
  "participants": [
    { "id": 12, "identifier": "user-abc", "name": "Alice B.",
      "created_at": "...", "updated_at": "..." }
  ],
  "sessions": [
    {
      "id": 30, "experiment_id": 20, "participant_id": 12,
      "status": "pending", "created_at": "...", "updated_at": "...",
      "session_data": {}
    }
  ],
  "chat_messages": [
    {
      "id": 50, "session_id": 30, "role": "human",
      "content": "Hello", "created_at": "..."
    }
  ],
  "traces": [
    { "id": 60, "experiment_id": 20, "session_id": 30, "created_at": "...", "data": {} }
  ],
  "participant_data": [
    { "id": 70, "participant_id": 12, "experiment_id": 20,
      "data": {}, "created_at": "...", "updated_at": "..." }
  ],
  "custom_tagged_items": [
    { "id": 80, "tag_id": 8, "content_type": "experiments.experimentsession",
      "object_id": 30, "created_at": "..." }
  ]
}
```

`has_more: true` means there are more rows beyond `next_cursor`; the caller should continue
paginating before waiting for new data. When `has_more: false`, the sync engine can record
`next_cursor` as its checkpoint and wait before the next poll.

`participants` ride the same stream because they grow with usage, but `sessions`, `participant_data`,
and any `custom_tagged_items` targeting a participant FK-reference them. A participant is always
created before the rows that reference it (`participant.created_at <= session.created_at`), so it
appears in the same page or an earlier one — never a later one. The sync engine must therefore apply
`participants` **before** the other arrays within each batch so the `FKTranslation` mapping exists
when the referencing rows are upserted.

### 5. `GET /api/v2/sync/file-chunk-embeddings/?cursor=<id>&limit=<n>`

Returns the team's `FileChunkEmbedding` rows — chunk text plus its vector — in primary-key order,
paginated. This is **structural** data pulled once during a structural sync, not part of the
living-data delta stream. It is split out from `export-team` because the rows are numerous and
large; transferring them lets the target skip re-running the embedding model.

Each row references `file_id` and `collection_id`, so this endpoint must be consumed **after**
`export-team` (which creates the `File` and `Collection` records the sync engine remaps via
`FKTranslation`).

**Cursor semantics:** `cursor` is the last `id` returned; rows are filtered `id > cursor` and
ordered by `id`. Primary keys are immutable, so — unlike living-data — there is no boundary
overlap. `next_cursor` is the maximum `id` in the response.

**Response:**

```json
{
  "cursor": 0,
  "next_cursor": 5000,
  "has_more": true,
  "file_chunk_embeddings": [
    {
      "id": 100, "file_id": 11, "collection_id": 10,
      "chunk_number": 0, "page_number": 1,
      "text": "Our refund policy ...",
      "embedding": [0.0123, -0.0456, "... (one float per dimension)"]
    }
  ]
}
```

### Secrets

Each provider's `config` is a JSON object stored encrypted at rest with the source environment's
key. Rather than singling out individual sensitive keys, the export treats the **entire `config`
object as opaque**: it is decrypted with the source key and re-encrypted with the target's public
key as one blob. `collections[].document_sources[].config` (in export-team) and the chatbot export's
`channels[].extra_data` are handled the same way. This keeps the source and target code agnostic to
each provider type's field layout.

To transfer a `config`:

1. The **target** generates one RSA keypair per sync run, before calling `export-team`.
2. The target passes its **public key** as `?public_key=<base64-DER>` on every request that returns
   secrets — `export-team` and each `chatbots/{id}/export`.
3. The **source** decrypts `config` with its own env key and re-encrypts the whole object with the
   target's public key, returning the ciphertext as the `config` value.
4. The **target** decrypts `config` with its private key and re-encrypts it under its own env key
   on insert.

Because a `config` can exceed RSA's direct size limit (e.g. the Vertex AI service-account JSON),
the public key seals it with **envelope encryption** — a random symmetric key encrypts the JSON and
the RSA public key wraps that symmetric key. The exact envelope format is an implementation detail;
to the caller, `config` is an opaque base64 string.

Plaintext secret values never appear in the response body. The keypair is ephemeral (generated per
sync run and discarded after import). Fields outside `config` are not secret and are sent as
plaintext — including a provider's `name`/`type` and a messaging provider's `extra_data` (which
holds only derived values such as Meta's `verify_token_hash`).

### Authorization

The `/api/v2/sync/` endpoints (`export-team`, `chatbots/{id}/export`, `living-data`,
`file-chunk-embeddings`) require a **superuser-level or dedicated migration credential**. They
expose the full team's data including re-encrypted secrets, so they must be tightly authorized,
strictly team-scoped, and audited. See `docs/agents/django_view_security.md`.

The standalone v2 pipeline endpoint (`GET /api/v2/pipelines/{id}/`) uses the existing v2 auth and
team-scoping machinery unchanged.

### Model → source-API mapping

Every model registered in the source app is accounted for below, tagged with the endpoint that
produces it. Legend: **export-team** = endpoint 1; **chatbot-export** =
`GET /api/v2/sync/chatbots/{id}/export/` (endpoint 2); **pipeline** = `GET /api/v2/pipelines/{id}/`
(endpoint 3 — all pipelines, chatbot-attached and standalone, referenced by `pipeline_id`);
**living-data** = endpoint 4; **embeddings** = endpoint 5 (`file-chunk-embeddings`); **—** = not synced.

| Model | Source | Notes |
| --- | --- | --- |
| `teams.team` | export-team | `team{}` |
| `teams.membership` | export-team | `memberships[]` |
| `teams.invitation` | — | pending invites not migrated |
| `teams.flag` | export-team | `team.feature_flags` |
| `users.customuser` | export-team | `users[]` — email, username, names; no password |
| `service_providers.llmprovider` | export-team | `config` re-encrypted |
| `service_providers.voiceprovider` | export-team | `config` re-encrypted |
| `service_providers.messagingprovider` | export-team | `config` re-encrypted |
| `service_providers.authprovider` | export-team | `config` re-encrypted |
| `service_providers.traceprovider` | export-team | `config` re-encrypted |
| `service_providers.llmprovidermodel` | export-team | team-scoped + global, flagged `is_global`; global matched not recreated |
| `service_providers.embeddingprovidermodel` | export-team | team-scoped + global, flagged `is_global`; global matched not recreated |
| `experiments.syntheticvoice` | export-team | `synthetic_voices[]`; team-scoped + global, flagged `is_global`; global matched not recreated |
| `custom_actions.customaction` | export-team | `custom_actions[]` |
| `experiments.consentform` | export-team | `consent_forms[]` |
| `experiments.sourcematerial` | export-team | `source_materials[]` |
| `experiments.survey` | export-team | `surveys[]` |
| `experiments.participant` | living-data | `participants[]`; grows with usage, applied before referencing rows |
| `annotations.tag` | export-team | `tags[]` (tag definitions) |
| `documents.collection` | export-team | `collections[]` |
| `documents.collectionfile` | export-team | `collections[].files[].collection_file` |
| `documents.documentsource` | export-team | `collections[].document_sources[]`; `config` re-encrypted |
| `files.file` | export-team | metadata only; content via file-content API |
| `ocs_notifications.eventtype` | export-team | `notification_event_types[]` |
| `ocs_notifications.usernotificationpreferences` | export-team | `user_notification_preferences[]` |
| `experiments.experiment` | chatbot-export | all versions (working + published), grouped by working version in `export-team.experiment_versions`; working version created first so others can set `working_version_id` |
| `bot_channels.experimentchannel` | chatbot-export | full `extra_data` (re-encrypted) embedded in chatbot-export; experiment implicit; webhook registration still manual |
| `events.statictrigger` | chatbot-export | embedded event |
| `events.timeouttrigger` | chatbot-export | embedded event; includes `config_changed_at` |
| `events.eventaction` | chatbot-export | embedded event action; `id` carried for `FKTranslation` |
| `pipelines.pipeline` | pipeline | endpoint 3, all versions (working + published) of both chatbot-attached and standalone pipelines, grouped by working version in `export-team.pipeline_versions`; working version created first |
| `pipelines.node` | pipeline | nodes + edges |
| `custom_actions.customactionoperation` | pipeline | node-attached operations (assistant-attached not synced) |
| `chat.chat` | living-data | conversation container |
| `chat.chatmessage` | living-data | `chat_messages` |
| `chat.chatattachment` | living-data | attachment metadata |
| `experiments.experimentsession` | living-data | `sessions` |
| `experiments.participantdata` | living-data | `participant_data` |
| `trace.trace` | living-data | `traces` |
| `annotations.customtaggeditem` | living-data | `custom_tagged_items` |
| `annotations.usercomment` | living-data | user comments on objects |
| `pipelines.pipelinechathistory` | living-data | pipeline chat history |
| `pipelines.pipelinechatmessages` | living-data | pipeline chat messages |
| `events.scheduledmessage` | living-data | per-participant scheduled state |
| `ocs_notifications.notificationevent` | living-data | generated notifications |
| `ocs_notifications.eventuser` | living-data | generated inbox state |
| `files.filechunkembedding` | embeddings | endpoint 5 (bulk vector export) |
| `assistants.openaiassistant` | — | excluded by decision; referencing nodes get a dangling FK |
| `assistants.toolresources` | — | excluded by decision |
| `mcp_integrations.mcpserver` | — | excluded by decision; referencing nodes get a dangling FK |
| `oauth.oauth2application` | — | OAuth deferred |
| `oauth.oauth2grant` | — | OAuth deferred |
| `oauth.oauth2accesstoken` | — | OAuth deferred |
| `oauth.oauth2refreshtoken` | — | OAuth deferred |
| `oauth.oauth2idtoken` | — | OAuth deferred |
| `oauth2_provider.devicegrant` | — | OAuth deferred |
| `socialaccount.socialaccount` | — | OAuth deferred |
| `socialaccount.socialapp` | — | global social-login config |
| `socialaccount.socialtoken` | — | OAuth deferred |
| `slack.slackoauthstate` | — | channel re-registration is manual |
| `slack.slackinstallation` | — | channel re-registration is manual |
| `slack.slackbot` | — | channel re-registration is manual |
| `evaluations.*` (all 9 models) | — | future scope |
| `human_annotations.*` (all 4 models) | — | future scope |
| `analysis.transcriptanalysis` | — | future scope |
| `analysis.analysisquery` | — | future scope |
| `assessments.score` | — | future scope |
| `events.eventlog` | — | trigger execution log |
| `events.scheduledmessageattempt` | — | delivery attempt log |
| `documents.documentsourcesynclog` | — | sync history |
| `experiments.promptbuilderhistory` | — | UI scratch history |
| `dashboard.dashboardcache` | — | cache |
| `dashboard.dashboardfilter` | — | user UI saved filter |
| `filters.filterset` | — | user UI saved filter |
| `banners.banner` | — | global admin banner |
| `sso.ssosession` | — | session state |
| `api.userapikey` | — | hashed; re-issued on target |
| `rest_framework_api_key.apikey` | — | hashed; re-issued on target |
| `auth.group` | — | seeded on target; referenced by name in memberships |
| `auth.permission` | — | Django built-in; seeded on target |
| `contenttypes.contenttype` | — | Django built-in; generic-FK refs sent as `app_label.model`, remapped |
| `taggit.tag` | — | not used directly; concrete table is `annotations.tag` |
| `taggit.taggeditem` | — | not used directly; concrete table is `annotations.customtaggeditem` |
| `waffle.flag` | — | global flag def; team link exported as `team.feature_flags` |
| `waffle.sample` | — | global |
| `waffle.switch` | — | global |
| `account.emailaddress` | — | allauth; verification re-established on target |
| `account.emailconfirmation` | — | transient verification token |
| `mfa.authenticator` | — | per-user MFA secret; user re-enrolls |
| `sessions.session` | — | session state |
| `sites.site` | — | deployment config |
| `site_admin.ocsconfiguration` | — | global instance config |
| `admin.logentry` | — | Django admin log |
| `field_audit.auditevent` | — | audit history |
| `silk.*` | — | profiling data |
| `django_celery_beat.*` | — | operational task scheduling |
| `data_migrations.custommigration` | — | migration tracking |
| `debug_toolbar.historyentry` | — | debug data |

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

- How will FKs to global resources created by the setup work, e.g. LLM models / synthetic voices?
  - **Resolved**: export-team returns global rows alongside team-scoped ones, flagged `is_global`.
    Globals are not recreated — the importer matches each to the target's already-seeded row by
    natural key and records the mapping in the FK translation table.
