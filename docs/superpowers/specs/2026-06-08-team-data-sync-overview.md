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

The full endpoint schemas live in `2026-06-09-team-data-sync-read-api-design.md`. This doc covers,
per step, the call we make, the response shape, how we use it, and how it feeds the FK table.

## Step 1 - General resources

**Call.** `GET /api/v2/sync/export-team/?public_key=<base64>`. The target generates an ephemeral RSA
keypair per run; the source re-encrypts every provider `config` (and other secret blobs) under the
public key (see the read-API doc's Secrets section).

**Not exported:**

- **Assistants** and **MCP servers** — excluded by decision; a referencing pipeline/chatbot node
  keeps a dangling FK (handled out of band).
- **Audit logs** — not needed.
- **Evaluations** and **human-annotation** review data — future scope. (Tags and tagged items *are*
  synced — in this step and Step 5.)
- OAuth/social login, hashed API keys, and Slack installs are re-established or re-registered on the
  target, not migrated. See the read-API mapping table for the exhaustive list.

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
