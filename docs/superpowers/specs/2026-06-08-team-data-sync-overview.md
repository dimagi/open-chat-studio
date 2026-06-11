---
status: stable
---

# Team Data Sync — Overview

## Glossary

- **Source Server**: The server being migrated from.
- **Target Server**: The server being migrated to.

## Overview

The migration process runs as a management command on the target server. This command fetches data
from the source server over HTTPS and recreates it locally using the ORM.

The following APIs will be used. Endpoints marked `*` are new and served under the `/api/v2/sync/`
prefix; the pipeline read (marked `†`) is also new but lives outside that prefix; the Files API
already exists:

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
- `†` **Pipeline read** — `GET /api/v2/pipelines/{id}/`: Fetch a pipeline's raw graph
  (nodes + edges with full params). **Not yet built** — no `pipelines` route exists in the v2 API
  today; to be provided by the planned v2 pipeline write API (outside the `/api/v2/sync/` prefix),
  reusing the react-flow serialization in `apps/pipelines/flow.py`. See endpoint 3.
- `*` **Living data** — `GET /api/v2/sync/living-data/<resource>/`: A family of keyset-paginated,
  per-resource delta endpoints for the data that grows with chatbot interactions (participants,
  sessions, messages, scheduled messages, notifications, annotations, …).

The migration runs in seven steps: (1) general resources, (2) file hydration, (3) pipeline creation,
(4) chatbot and channel creation, (5) live data, (6) parity check, (7) cutover.

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
  target, not migrated — no synced model depends on them, and `AuthProviderType` has no OAuth variant,
  so outbound custom-action auth (api-key/bearer, stored in the encrypted `config`) is unaffected. The
  migration instead emits a **cutover re-establishment checklist**: the team's `OAuth2Application`
  registrations to recreate and the users relying on social login / MFA to re-authenticate, so these
  are surfaced rather than silently dropped — the same treatment as channel webhooks and Slack
  installs. See the Model → source-API mapping table in the Read API Reference section below for the
  exhaustive list.

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

Live data is the tables that grow with chatbot interactions. Rather than one combined endpoint, each
related group is served by its **own** keyset-paginated endpoint under `/api/v2/sync/living-data/`,
consumed in **dependency order** so referencing rows are always created after their targets:

1. `participants` + `participant_data`
2. `sessions` + `chats` + `chat_attachments` (plus the `File` rows backing those attachments)
3. `chat_messages` + `traces`
4. `pipeline_chat_history` + `pipeline_chat_messages`
5. `scheduled_messages`
6. `notification_events` + `event_users`
7. `custom_tagged_items` + `user_comments` (last — generic FKs that may point at any of the above)

**Pagination (keyset, per resource).** Append-only resources (`chat_messages`, `traces`,
`chat_attachments`, `pipeline_chat_messages`, `custom_tagged_items`, `notification_events`) paginate
by **primary key** (`id > cursor`) — immutable and monotonic, so no boundary overlap. Mutable
resources (`participants`, `sessions`, `participant_data`, `scheduled_messages`, `user_comments`,
`event_users`) paginate by the **composite keyset `(updated_at, id) > (cursor_ts, cursor_id)`** so a
run of rows sharing one `updated_at` is paged deterministically — never skipped (truncation) nor
re-served forever (timestamp ties). Each endpoint returns its own `next_cursor` + `has_more` and is
polled until cutover. (See endpoint 4 in the Read API Reference for the per-resource cursor rules.)

**Use.** Upsert each row by source pk (idempotent) and record its `target_key`; write the source
`created_at`/`updated_at` back with raw SQL after the upsert (timestamp rule). FK refs are remapped
through `FKTranslation` by field introspection (see *Serialization, FK remapping, and guard tests*).
Two resource-specific notes:

- **`ParticipantData.data` and `ParticipantData.encryption_key` are encrypted at rest** — the only
  living-data secrets. They travel through the same decrypt → re-encrypt-under-`public_key` path as
  provider `config` (see [Secrets](#secrets)), so the `participants` endpoint also accepts the
  target's `public_key`.
- **`ChatAttachment.files` is an M2M to `File`.** export-team only sees files reached via
  collections, so attachment files are *living-data* files: each `chat_attachments` row carries its
  backing `File` metadata (created and content-hydrated as in Step 2) plus the remapped `file_id`
  list, which the importer applies with `.set()` after the attachment row exists.

**Firing gate (avoiding double-sends).** `poll_scheduled_messages` is a global, unconditional beat
task, so a synced `ScheduledMessage` with a due `next_trigger_date` would fire on the target **while
the source is still live**. The target therefore holds a **per-team "migrating" gate** that excludes
teams under migration from `get_messages_to_fire()` (and timeout-trigger firing), so synced
`ScheduledMessage` rows sit inert until cutover clears the gate. The cursors capture the source's
final firing state, so on resume the target never repeats an already-sent occurrence. Step 7 (Cutover)
defines the full suspend → flip → final-poll → clear-gate sequence.

## Step 6 - Parity check

When the migration is complete, no `FKTranslation` row may have a null `target_key` — every synced
source row must have a created target row. A null entry means a resource was missed or a dependency
was never created; the command can be re-run to fill the gaps.

**File content.** Under option A, the FK-table check covers files implicitly — each row's bytes were
fetched and stored as it was created. Under option B the bytes arrive out of band, so the check
instead spot-checks a sample of files (e.g. 20–50 across the team) and confirms each has content at
its expected storage key. Loading every file's bytes is unnecessary; a sample confirms the upload
landed.

## Step 7 - Cutover

Cutover is the single, team-wide switch from the source to the target. Until this point the source is
still live and the target — though fully built and kept current by Steps 1–5 — is held **inert** by
the per-team firing gate (Step 5). Cutover flips inbound traffic and outbound firing over to the
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
(Meta/Slack/SureAdhere — flipping it moves *every* bot on that provider at once). Email and CommCare
Connect are configured once at the **deployment level** and shared across teams, so a single-team
migration cannot flip them in isolation — they are an out-of-band ops task the command only reports.

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
3. **Keep polling through the window.** Until the last webhook is flipped, messages to not-yet-flipped
   channels still land on the source, so the living-data sync keeps running. The window in which some
   channels point at the target and others at the source is data-safe: source firing is frozen and the
   sync keeps pulling stragglers.
4. **Final poll once the source is quiet.** After the last channel is flipped (no new data can reach
   the source), run the final living-data poll so the target is fully caught up, including the frozen
   scheduled-message state.
5. **Clear the target gate.** Flip the team out of "migrating" mode; the target's
   `poll_scheduled_messages` resumes from the synced state. Because source firing was suspended and the
   final poll captured the source's last firing state, no scheduled occurrence is duplicated or
   skipped.

### Re-establishment checklist

Cutover also surfaces the resources that are re-established rather than migrated (see Step 1 and the
mapping table), so nothing is silently dropped:

- **OAuth2 applications** — the team's `OAuth2Application` registrations to recreate on the target;
  external API clients re-consent (client secrets are hashed and tokens are bearer secrets, so neither
  is migrated).
- **Social login / MFA** — users relying on social login or MFA re-authenticate / re-enrol.
- **Email / CommCare Connect** — instance-level inbound routing, handled out of band by ops.

## Read API Reference

Steps 1–7 describe the migration flow with abbreviated responses. This section is the full schema
reference for the five source-side read endpoints the sync command consumes — four new ones under
`/api/v2/sync/` plus the v2 pipeline read (also new, not yet built — see endpoint 3). Per the
timestamp rule, every row additionally
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

**Status: not yet built.** No `pipelines` route exists in the v2 API today — the only registered v2
surface is the `chatbots` viewset, and pipeline graph data is currently exposed only *embedded* in
the digested `/inspect/` projection. This endpoint must be built (or provided by the planned v2
pipeline write API), reusing the round-trippable react-flow serialization in `apps/pipelines/flow.py`.

Returns the raw pipeline graph (nodes + edges with full params) for a given pipeline id
(numeric — pipelines have no public UUID). This is the same round-trippable react-flow shape the
write API spec specifies for `GET /api/v2/chatbots/{id}/pipeline/` (`apps/pipelines/flow.py`), **not**
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

### 4. `GET /api/v2/sync/living-data/<resource>/?cursor=<keyset>&limit=<n>`

Living data is served as a **family of per-resource endpoints**, not one combined call. Each is
keyset-paginated and polled repeatedly until cutover; the engine consumes them in the dependency
order listed in Step 5. The `participants` endpoint (whose `participant_data` rows carry encrypted
fields) also accepts `?public_key=<base64>` (see [Secrets](#secrets)).

**Resource groups and keyset:**

| Endpoint (`<resource>`) | Rows returned | Keyset |
| --- | --- | --- |
| `participants` | `participants` + `participant_data` | `(updated_at, id)` |
| `sessions` | `sessions` + `chats` + `chat_attachments` (+ backing `File` rows) | `(updated_at, id)` |
| `messages` | `chat_messages` + `traces` | `id` (append-only) |
| `pipeline-history` | `pipeline_chat_history` + `pipeline_chat_messages` | history `(updated_at, id)`, messages `id` |
| `scheduled-messages` | `scheduled_messages` | `(updated_at, id)` |
| `notifications` | `notification_events` + `event_users` | events `id`, users `(updated_at, id)` |
| `annotations` | `custom_tagged_items` + `user_comments` | items `id`, comments `(updated_at, id)` |

**Cursor semantics.** Append-only resources filter `id > cursor` ordered by `id` — primary keys are
immutable and monotonic, so there is no boundary overlap. Mutable resources filter
`(updated_at, id) > (cursor_ts, cursor_id)` ordered by the same composite, so a run of rows sharing a
single `updated_at` is paged through deterministically rather than skipped (per-table truncation
under a shared timestamp cursor) or re-served forever (timestamp ties). `next_cursor` is the last
keyset value in the response; `has_more` indicates more rows beyond it. Upsert-by-source-pk keeps any
re-served boundary row a no-op.

**Response** (e.g. `sessions`):

```json
{
  "next_cursor": { "updated_at": "2026-06-09T14:32:00Z", "id": 30 },
  "has_more": true,
  "sessions": [
    { "id": 30, "experiment_id": 20, "participant_id": 12, "experiment_channel_id": 42,
      "status": "pending", "session_data": {}, "created_at": "...", "updated_at": "..." }
  ],
  "chats": [
    { "id": 71, "experiment_session_id": 30, "created_at": "...", "updated_at": "..." }
  ],
  "chat_attachments": [
    { "id": 80, "chat_id": 71, "tool_type": "code_interpreter", "extra": {},
      "file_ids": [501],
      "files": [ { "id": 501, "name": "out.csv", "content_type": "text/csv", "content_size": 2048 } ] }
  ]
}
```

Within a response the engine applies parents before children (`sessions` → `chats` →
`chat_attachments`). Across endpoints, the dependency order in Step 5 guarantees a referenced row
(e.g. a `participant`) was already created by an earlier endpoint's drain. `chat_attachments` create
their backing `File` rows from the embedded `files[]` metadata (content hydrated as in Step 2) before
applying the `file_ids` M2M with `.set()`.

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
key as one blob. `collections[].document_sources[].config` (in export-team), the chatbot export's
`channels[].extra_data`, and living data's `participant_data[].data` / `encryption_key` are handled
the same way. This keeps the source and target code agnostic to each provider type's field layout.

These secrets fall into two classes, guarded differently by the secret tripwire (see *Serialization,
FK remapping, and guard tests*): *encrypted at rest* — the five provider `config`s plus
`ParticipantData.data` / `encryption_key`, all detectable as `django_cryptography` `EncryptedMixin`
fields — and *plaintext at rest but sensitive by policy* — `documents.documentsource.config` and
`bot_channels.experimentchannel.extra_data`, which no field type marks as secret.

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

The standalone v2 pipeline endpoint (`GET /api/v2/pipelines/{id}/`), once built, uses the existing
v2 auth and team-scoping machinery unchanged.

### Serialization, FK remapping, and guard tests

Export and import are **field-introspection-driven**, so new model fields are handled without
per-field code:

- **Scalars / JSON** are serialized as-is and written back on insert.
- **Foreign keys** are remapped through `FKTranslation`: on import, walk `model._meta.get_fields()`
  and, for each `ForeignKey`, resolve `field.attname` → the related model's content type →
  `FKTranslation`. A new FK rides along correctly with no per-field list to maintain. (Refs to
  excluded resources — assistants, MCP — stay dangling by decision.)
- **Many-to-many** are exported as remapped pk lists and applied with `.set()`/`.add()` *after* the
  row exists (M2M cannot be set in the initial `create()`). *Bare* auto-through tables (e.g.
  `chat.ChatAttachment_files`) are handled entirely via `.set()`; *explicit* through models that
  carry extra columns (e.g. `documents.CollectionFile`) are synced as their own rows so those columns
  are not lost.

**Exhaustiveness guard.** A test enumerates `apps.get_models(include_auto_created=True)` — the
`include_auto_created` flag is required, since auto M2M through tables are otherwise invisible — and
asserts every model is classified in the mapping table below as either a sync category or an explicit
exclusion-with-reason. Adding a model fails the test until it is classified: the executable
counterpart of the mapping table, modelled on
`apps/teams/tests/test_permissions.py::test_missing_content_types`.

**Secret tripwire.** Two prongs guard against leaking secrets as plaintext:

- *Prong A — encrypted-at-rest (automatic).* Secrets are `django_cryptography` fields, detectable via
  `isinstance(field, EncryptedMixin)`. The secret path is introspection-driven over those fields, and
  a behavioural test runs each synced model through the exporter and asserts every encrypted field
  comes out as ciphertext, never plaintext (the library decrypts transparently on read, so naive
  serialization *would* leak). Covers the five provider `config`s and `ParticipantData.data` /
  `encryption_key`.
- *Prong B — sensitive plaintext (declared).* Some fields are plaintext at rest but sensitive by
  policy (`bot_channels.experimentchannel.extra_data`, `documents.documentsource.config`) and cannot
  be detected by type. A field-snapshot baseline over the secret-carrying models trips CI whenever a
  field is added to one of them, forcing a classify-or-acknowledge decision (add to a
  `SENSITIVE_PLAINTEXT_FIELDS` allowlist, or bump the baseline).

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
| `files.file` | export-team / living-data | metadata only; content via file-content API. Collection files via export-team; chat-attachment files ride living-data (`chat_attachments`) |
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
| `users.customuser_groups` (auto M2M) | — | global Django groups; skipped — team authz flows via `teams.membership.groups`; carrying these risks cross-team/admin escalation on the target |
| `users.customuser_user_permissions` (auto M2M) | — | direct per-user permissions; skipped — perms flow via groups; re-established deliberately on the target |
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

### Resolved

- **Keeping the API current as models are added** — a guard test enumerates
  `apps.get_models(include_auto_created=True)` and fails until each model is classified in the
  mapping table; serialization is field-introspection-driven (scalars, FKs, and M2M) so new fields
  ride along; a two-pronged secret tripwire guards encrypted-at-rest and sensitive-plaintext fields.
  See *Serialization, FK remapping, and guard tests*.
- **Scheduled messages** — synced as living data (`scheduled-messages` endpoint) with a per-team
  firing gate so the target does not double-fire during the migration window. See Step 5.
- **Paginating live data** — per-resource endpoints consumed in dependency order, keyset-paginated
  (pk for append-only, `(updated_at, id)` for mutable). See Step 5 and endpoint 4.
- **OAuth models** — deferred; re-established on the target via a cutover checklist (same bucket as
  Slack/webhook re-registration), since no synced model depends on them and there is no OAuth
  auth-provider type. See Step 1 and the mapping table.
- **Chatbot switchover** — a single team-wide cutover (Step 7), not bot-by-bot: auto-flip what we can
  (Telegram/Twilio), report the provider-level flips (Meta/Slack/SureAdhere) and per-channel manual
  flips (Turn.io/Web/API), and treat email + CommCare Connect as instance-level ops. The firing gate
  stays per-team. See Step 7 (Cutover).

All open questions are now resolved.
