---
status: active
---

# Per-chatbot team sync

> Design document for making `manage.py sync_team` move a selected set of chatbots rather than a
> whole team. A team admin marks the exportable chatbots when they enable migration mode; the source
> server applies that selection as an additional filter on every export resource.
>
> `status: active` — still evolving; ADR extraction is gated off until this flips to `stable`.

Scope of the change: `manage.py sync_team` currently moves an entire team. We want it to move a
*selected set of chatbots* — each chatbot's versions, pipeline, nodes, channels, triggers, sessions,
chats, messages, attachments, participants, participant data, traces, scheduled messages, and the
team-level resources those actually reference (providers, collections, files, source material,
consent forms, custom actions). Evaluations, human annotations and transcript analyses are out of
scope.

## What exists today

The sync is a three-part system, all driven off one manifest:

| Piece | File | Role |
|---|---|---|
| Manifest | `apps/teams/export/manifest.py` | The single maintenance surface: model list in dependency order, per-model config, and `team_scoped_queryset()` (`manifest.py:243`) which builds the queryset the source serves |
| Source API | `apps/api/export/views.py`, `urls.py` | One literal path per resource, generic `ResourceView.get` (`views.py:99`), keyset pagination, team resolved from the API key |
| Client + importer | `apps/teams/export/client.py`, `importer.py`, `translation.py`, `management/commands/sync_team.py` | Walks the manifest in order, pages each resource, remaps FKs through a SQLite translation store, records checkpoints |

Scoping is currently **one dimension only**: team. `TEAM_PATH_REGISTRY` (`manifest.py:146`) gives the
ORM path from each model to its owning team; everything else follows.

## The core decision: how the selection reaches the source

The selection lives on the **team, on the source server**, set by a team admin when they enable
migration mode: `Team.exportable_experiments`, an M2M to `Experiment`. Empty means "export
everything". The export API reads it off `request.team`; the client sends nothing.

The alternative — a repeatable `?chatbot=<public_id>` query param driven by a `--chatbot` flag on
the command — was rejected. It makes the selection a client preference with no authorization behind
it: anyone holding the API key could pull any chatbot. A server-side allowlist matches the shape
already in place, where `is_migrating` and `public_key` are server-side gates the client only reads
(`sync_team.py:104`). The command gets no chatbot flag at all: it syncs whatever the source allows.

Either way the filter is applied in the same place: a second dimension on `team_scoped_queryset()`.

```python
def scoped_queryset(entry, team, chatbot_scope=None) -> QuerySet
```

`_paginate` takes a queryset, so pagination is untouched.

### What the allowlist buys

1. **No query params on ~60 resource endpoints.** `ResourceView.get` (`apps/api/export/views.py:99`)
   reads the scope off `request.team`. The OpenAPI surface barely moves — only the team serializer
   changes — instead of adding a documented parameter to every resource path in `resource_view:208`.
2. **The migration freeze narrows to the selected chatbots.** Today `migrating_team_ids()`
   (`apps/teams/export_service.py`) stops *every* trigger and scheduled message the team owns
   (`apps/events/tasks.py:37,54,81,105`) — far too blunt when one chatbot is moving. With the
   selection on the server it becomes a per-experiment question; see
   "Narrowing the migration freeze" below.
3. **Audited for free.** The field joins `TEAM_FIELDS` in `apps/teams/model_audit_fields.py` next to
   `is_migrating` and `public_key`, so changing what may leave the server is recorded.
4. The client stops resolving chatbot ids and handling 404s on unknown ones.

### Decisions the allowlist forces

**Empty vs. all.** These only differ for a chatbot created *after* the selection: "none" exports it,
an explicit full list doesn't. Store exactly what the admin picked and treat only empty as
"everything" — one less special case, and the UI can still say "all chatbots selected". Collapsing a
full selection to empty means a chatbot created mid-migration silently becomes exportable.

**Storage shape.** M2M to `Experiment` rather than an `ArrayField` of ids: FK integrity, cascade on
chatbot delete, and it composes into the scope subqueries directly. New table, so the migration is
backwards compatible (no `NOT NULL` column trap).

**It must be excluded from the export.** `EXCLUDE_REGISTRY["teams.team"]` (`manifest.py:122`) already
drops `is_migrating`, `public_key`, `files_export*`. The new field joins them: it's per-server
operational state, and `load_team` imports the team row *before* any experiment exists, so the
importer could not translate those FKs anyway.

**Changing the allowlist mid-migration is invisible to the client.** This is the one thing the
allowlist makes worse than a client flag. Cursors are derived from rows already synced
(`sync_team.py:_start_cursor:90`), so if an admin adds chatbot B after A has finished, the source
starts serving B's rows and the client resumes each resource from A's cursor, skipping every B row
below it. Fix: expose the selection on `GET /api/export/team/`
(`apps/api/export/serializers.py:209`), have the client store a fingerprint of it in the state DB,
and reset cursors — keeping the FK map — when it changes.

**The allowlist says which chatbots, not which models.** Evaluations, annotations and analyses still
need the manifest-level classification below, and those resources must serve empty while a selection
is active, or an eval row arrives referencing an experiment that was never synced and `resolve_fk`
raises.

### What selecting a chatbot means

The picker shows **working versions only** — `working_chatbots(team)` (`apps/api/v2/lookups.py:20`)
already returns exactly that set — and the M2M holds working versions only, validated in the form.
Selecting one selects the whole family: every published version, and the archived ones too. The scope
builder expands it:

```python
Q(pk__in=selected) | Q(working_version__in=selected)
```

Archived rows need no special handling. `team_scoped_queryset` already reads through `_base_manager`,
which bypasses the `is_archived=False` default manager.

The same expansion applies to the versioned children and to the versioned shared resources:
`Pipeline`, `Node` and the three trigger models, plus `ConsentForm`, `SourceMaterial`, `Collection`
and `File`, all carry a `working_version` FK. Whenever a published copy is in scope its working
version must be too, or the self-referential FK cannot resolve — the manifest relies on the working
version having the lower pk so it is served, and imported, first (`manifest.py:29`).

Two consequences worth putting in the UI help text. A version published after the admin makes the
selection is automatically in scope, which is what you want. And a chatbot archived *after* being
selected stays selected and stays exportable: `working_chatbots()` filters archived rows out of the
picker, not out of the M2M. Migrating an archived chatbot is a reasonable thing to want.

### Narrowing the migration freeze

`migrating_team_ids()` gives way to a Q builder in `apps/teams/export_service.py`:

```python
def frozen_experiment_q(path: str = "experiment") -> Q:
    """Experiments whose outbound firing is frozen by migration mode."""
```

It unions two groups, because an empty allowlist still means "everything": a migrating team with no
selection has all of its chatbots frozen, as today, while a migrating team with a selection freezes
only the listed families. The family expansion is required — triggers are versioned alongside
their chatbot, so a trigger on version 3 points at the version-3 row, not the working version the
allowlist holds:

```python
Q(**{f"{path}__in": allowed}) | Q(**{f"{path}__working_version__in": allowed})
```

All four call sites in `apps/events/tasks.py` become `.exclude(frozen_experiment_q())`. The
`ScheduledMessage` one (`:105`) currently excludes on `team_id`, but it has its own `experiment` FK,
so it takes the same shape as the other three. These are forward FK chains, so nothing multiplies
rows and ADR-0037 does not apply.

## The scope model

Manifest models fall into three classes under a chatbot selection.

The paths below were found by walking the model graph while scoping this work; they are a starting
point for a **hand-written** registry, not something to derive at runtime. The shortest path is not
always the right one — `evaluations.evaluationmessage` has a shortest path of `session__experiment`
but needs three branches, as its existing `TEAM_PATH_REGISTRY` entry already shows — and several
paths cross nullable FKs, where "no path" and "path to null" mean different things. Static entries
are reviewable in a diff, and the existing partition test can force a decision when a model is added.

### (a) Owned — filter by a path to the experiment family

| Model | Path |
|---|---|
| `experiments.experiment` | `Q(pk__in=ids) \| Q(working_version__in=ids)` |
| `bot_channels.experimentchannel` | `experiment__in` (+ team web/API channels referenced by synced sessions) |
| `events.{static,timeout,scheduled}trigger` | `experiment__in` |
| `events.eventaction` | `static_trigger__experiment__in` / `timeout_trigger__experiment__in` / `scheduled_trigger__…` |
| `events.scheduledmessage` | `experiment__in` |
| `experiments.experimentsession` | `experiment__in` |
| `experiments.participantdata` | `experiment__in` |
| `experiments.participant` | `experimentsession__experiment__in` (distinct) |
| `chat.chat` | `experiment_session__experiment__in` (reverse O2O) |
| `chat.chatmessage`, `chat.chatattachment` | `chat__experiment_session__experiment__in` |
| `trace.trace` | `experiment__in` |
| `pipelines.pipelinechathistory` | `session__experiment__in` |
| `pipelines.pipelinechatmessages` | `chat_history__session__experiment__in` |
| `cost_tracking.usagerecord` | `experiment__in` |
| `annotations.customtaggeditem`, `annotations.usercomment`, `assessments.score` | generic FK — union over content types (chat / chatmessage / experimentsession) |

Those generic-FK rows are built from the **same querysets** that scope the `chats`,
`chat_messages` and `sessions` resources, never from an independently written path:

```python
Q(content_type=ct_chat, object_id__in=scope.chats)
| Q(content_type=ct_message, object_id__in=scope.chat_messages)
| Q(content_type=ct_session, object_id__in=scope.sessions)
```

`_resolve_generic_fks` raises `UnresolvedForeignKey` rather than nulling, so a filter even slightly
wider than what was synced aborts the import mid-run. Deriving both from one place makes that drift
impossible by construction.

Participants are shared: one synced for chatbot A is reused by chatbot B. That is correct, and it is
what makes a single FK translation store per team necessary. Because a participant who first talked
to another chatbot joins the scope without their row changing, the registry classifies
`experiments.participant` as referenced rather than owned, so a selection re-reads it in full like
the resources in (b).

### (b) Referenced — a reverse closure, not a path

Shared team resources pulled in by what the chatbot uses:

```
pipelines.pipeline      experiment.pipeline ∪ EventAction.params["pipeline_id"] ∪ their working_versions
pipelines.node          pipeline__in
documents.collection    node.collection ∪ node.collection_indexes ∪ working_versions
files.file              collection.files ∪ documentsource.files ∪ chatattachment.files
                        ∪ syntheticvoice.file ∪ working_versions
custom_actions.*        customactionoperation.node__in → custom_action
service_providers.*     node.llm_provider / llm_provider_model / synthetic_voice
                        ∪ experiment.voice_provider / synthetic_voice / trace_provider
                        ∪ collection.llm_provider / contextualizer_llm_provider /
                          reranker_provider / contextualizer_llm_model / embedding_provider_model
                        ∪ channel.messaging_provider ∪ customaction.auth_provider
                        ∪ documentsource.auth_provider
experiments.sourcematerial / consentform    node.source_material / experiment.consent_form + families
documents.collectionfile, documentsource, files.filechunkembedding    collection__in
annotations.tag         all of the team's tags — they are small, and narrowing them only adds
                        a way for a tagged item's FK to fail
users.customuser        keep team-wide (cheap, and several FKs to it are NOT NULL)
cost_tracking.pricingrule, ocs_notifications.*    keep team-wide. Notification titles, messages
                        and links can name chatbots outside the selection; the settings form and
                        the sync report say so
```

### (c) Excluded entirely

Per the brief: `evaluations.*`, `human_annotations.*`, `analysis.*`. Note `assessments.score`
straddles: session scores are in (a), scores hanging off `evaluationresult`/`annotation` are in (c).

## Migration card UI

The selection goes on the existing **Migration public key** card in the *Data & migration* section
of team settings (`templates/teams/manage_team.html`, `#data`, team admins only). It stays one card
and one form (`TeamPublicKeyForm` gains `exportable_experiments`), because the key, the scope and
the mode are set together before a migration starts. The card is renamed **Migration** and the
button **Save**.

Clickable mockup: <https://claude.ai/artifact/AEBraLgMiwsY4RMUL4fF6k>. It opens in the state
before a migration starts: no key, all chatbots, migration mode off.

### Behaviour

- **Scope radio.** Two options make "empty means everything" explicit instead of relying on an empty
  picker. *All chatbots* submits an empty list and clears the M2M. *Only selected* enables the
  picker; submitting it with nothing picked is a form error ("Pick at least one chatbot, or choose
  All chatbots"). On load the radio reads the bound form value, not `request.team`, so a rejected
  public key re-renders with the admin's unsaved selection intact.
- **Picker.** TomSelect (`window.TomSelect`, same plugins as `TOM_SELECT_CONFIG` in
  `assets/javascript/dashboard/main.js`: `remove_button`, search on text) over a `SelectMultiple`.
  Options are `working_chatbots(team)` sorted by name, unioned with any chatbot already in the M2M
  that has since been archived, labelled "(archived)". Without that union the archived chatbot is
  missing from the picker, fails the form's queryset validation, and is silently dropped on the next
  save. The queryset is also the validation, so a published version id cannot be submitted. The full
  list is rendered into the page — no async search endpoint — since a team's chatbot count is in the
  tens to low hundreds.
- **Help text under the picker**: "Selecting a chatbot includes all of its versions, including ones
  published later. Chatbots created after you save are not included."
- **Select all / Clear.** *Select all* picks every current chatbot explicitly; it does not switch
  the radio to *All chatbots*. That is the empty-vs-all distinction above, so the "created after
  you save" sentence stays visible whenever *Only selected* is chosen.
- **Migration mode text** follows the radio: team-wide wording under *All chatbots*, "the N
  selected chatbots… Other chatbots keep running" under *Only selected*. The UI lands in sequencing
  step 3 and the narrowed freeze in step 6, so until step 6 the text stays team-wide in both cases.
- **Mid-migration warning.** Shown only while `is_migrating` is saved as on and the picker value
  differs from the saved one (Alpine compares against the initial value). It states the two
  consequences an admin cannot see: the client resets its cursors when the fingerprint changes, and
  removing a chatbot does not delete what was already synced.
- **Header badges.** The existing *Set / Not set* key badge, plus *Migration mode on* when saved on.
- **Success message.** "Migration settings saved." Audit comes from `TEAM_FIELDS`.

### Outside the card

- **App banner** (`templates/teams/migration_lock_banner.html`). With a selection: "3 chatbots in
  this team are being migrated. Do not edit them until the migration is complete." with a link to
  `#data`. It shows a count, not names, so the banner adds one `COUNT` query per page and nothing
  unbounded. Without a selection the current team-wide text stays.
- **Chatbot pages** are unchanged in this iteration. A per-chatbot "being migrated" badge is a
  follow-up if the team-wide banner turns out to be too vague.

## Files that change

**`apps/teams/models.py:73`** — `exportable_experiments` M2M to `Experiment`, plus the migration.
Added to `TEAM_FIELDS` in `apps/teams/model_audit_fields.py` and to
`EXCLUDE_REGISTRY["teams.team"]` in `manifest.py:122`.

**`apps/teams/forms.py:149,175`** (`TeamPublicKeyForm`, `TeamMigrationForm`) and
**`apps/teams/views/manage_team_views.py:180,219`** (`set_public_key`, `set_migration_lock`) — carry
the selection. `templates/teams/manage_team.html` needs a searchable multi-select on the Migration
card; a plain `<select multiple>` will not do for a team with 50 chatbots.

**`apps/teams/export_service.py` + `apps/events/tasks.py:37,54,81,105`** — `migrating_team_ids()`
gives way to `frozen_experiment_q()`, so only the selected chatbots stop firing.

**`apps/api/export/serializers.py:209`** (`build_team_serializer`) reports the selection;
`check_source_team_ready` (`sync_team.py:104`) prints it so the operator sees the server's answer
rather than assuming.

**`apps/teams/export/manifest.py`** — the bulk of the work.

- New `CHATBOT_PATH_REGISTRY` for class (a), mirroring `TEAM_PATH_REGISTRY:146`.
- New module (probably `apps/teams/export/chatbot_scope.py`) computing class (b) as nested querysets
  so it's one SQL statement, not materialised id lists — `chat_messages` for a busy bot can't take
  `pk__in`.
- `ManifestEntry` gains a scope classification (`owned` / `referenced` / `excluded`) so the client
  knows what to skip and the server can reject an excluded resource when a chatbot filter is present.
- `team_scoped_queryset` grows a scope argument.
- `GLOBAL_CONFIG:207` rows (LLM/embedding models, synthetic voices) currently serve *all* globals via
  `Q(team__isnull=True)`. Under a chatbot scope they should narrow to the referenced ones, or every
  unmatched global aborts the import with `MissingGlobalRow`.

**`apps/api/export/views.py`** — `ResourceView.get:99` reads
`request.team.exportable_experiments`, builds the scope from it, and passes it to the queryset
builder. An excluded resource (class (c)) returns an empty page while a selection is active.
`resource_view:208` is untouched — no new query parameter to document.

**`apps/api/export/urls.py`** — unchanged.

**`apps/teams/export/client.py`** — unchanged; the scope is server-side.

**`apps/teams/export/importer.py`** — `_remap_embedded_resource_ids` learns `events.eventaction`
and rewrites `params["pipeline_id"]` through the store. Manifest order already imports pipelines
before event actions, so the translation exists by the time the action lands. This is a standing bug
in the full-team sync rather than new work for the partial one, so it lands first, on its own.

**`apps/teams/export/translation.py` + `sync_team.py:_start_cursor:90`** — **this is the part that
actually breaks.** Cursors are derived from rows already committed for a model (`committed_targets`),
not stored. Sync chatbot A, then chatbot B against the same team store: the derived pk cursor is
`max(A's session ids)`, so every B row with a lower id is skipped. Same for the `updated_at_id`
cursor. Fix: an explicit `cursors` table in the SQLite store keyed by `(selection_key, model_label)`,
written *after* each page's rows commit. The FK translation table must stay shared across selections —
that's what lets the second chatbot reuse the providers and files the first one created.

**`apps/teams/management/commands/sync_team.py`**

- `run_sync:237` — skip class (c) entries; fingerprint the server's selection and reset cursors
  when it changes.
- `force_delete_team:274` — currently deletes the whole team, which would destroy chatbots synced
  under an earlier selection. Needs a per-chatbot variant, or a refusal whenever the source reports a
  non-empty allowlist.
- `_report:364` — name the chatbots synced and state plainly that evaluations, annotations and
  analyses were not.
- `check_sync_preconditions:128` — the files-confirmation prompt is team-wide; for a partial sync the
  per-file backfill path (`Importer._handle_missing_object`) may be the better answer, since
  `create_team_files_zip_task` only zips whole teams.

**`apps/teams/management/commands/reregister_webhooks.py`** — takes `--team-slug` only; it would
repoint webhooks for chatbots still live on the source. Needs to be limited to the chatbots actually
synced (a `--chatbot` flag, or read from the target's sync state).

**Tests** — `apps/teams/export/tests/test_manifest.py` (its partition tests need to cover the new
registries the same way they cover `TEAM_PATH_REGISTRY`), `test_command.py`,
`apps/api/export/tests/test_views.py`.

**Generated** — `api-schemas/export.yml` regenerates via CI; the schema checksum changes, so both
servers need the new code (which the existing preflight already enforces).

## Performance

Cost is dominated by the high-volume resources — `chat_messages`, `chats`, `sessions`,
`participants`, `traces` — and every per-page cost is multiplied by `rows / limit` pages. The items
below are ordered by expected impact; the measurements are from this branch.

### The missing `updated_at` indexes come first

Every `updated_at_id` resource paginates with `ORDER BY updated_at, id`, and **none of the 23 models
using that cursor has an index leading with `updated_at`**. `ChatMessage.Meta.indexes`
(`apps/chat/models.py:191`) carries `(chat, created_at)`, `(chat, message_type, created_at)`,
`(created_at)` and a GIN index on `external_ids` — nothing the sort can use. Each page is therefore a
scan plus a top-N sort of the whole scoped set, giving `rows / limit` sorts of the table per run.

This already holds for the full-team sync. The chatbot filter makes it worse: the planner loses the
`team_id` prefix it could otherwise lead with.

Add `models.Index(fields=["updated_at", "id"])` to the in-scope models before any of the scope work
lands. With it the chatbot filter costs about one ordered pass over the table per sync — index scan
in cursor order, nested-loop probe up the join, stop at `limit + 1` matches — rather than one sort
per page. Without it there is no baseline against which to judge anything else here.

### Materialise the experiment family; keep only the large sets as subqueries

`chat__experiment_session__experiment__in` with `ORDER BY updated_at, id` and `LIMIT 101` is the
abort-early misestimation case. Postgres chooses between an index scan in cursor order that probes
the join per row and stops early (one table pass per sync) and a hash semi-join over the scoped
messages followed by a sort (proportional to the scoped rows *per page*). It picks the second
whenever it under-estimates the join's selectivity.

Two things bias it towards the first. Resolve `Q(pk__in=selected) | Q(working_version__in=selected)`
to a list of ids once per request and pass that down: the family is tens of rows, so every dependent
filter becomes `experiment_id IN (...)` against an indexed FK column, which the planner estimates
well. Only the session-, chat- and message-sized sets need to stay nested querysets — the note under
"Files that change" about `pk__in` not scaling applies to those, not to the family itself.

And raise the page size. `MAX_LIMIT` is already 1000 (`apps/api/export/views.py:40`) while
`sync_team` defaults to 100, so every per-page cost in this section divides by ten at no cost.

If the plan is still wrong under `EXPLAIN` on production-shaped data, the structural answer is a
denormalised `experiment_id` on `Chat`, which holds the `OneToOneField` to `ExperimentSession` and so
can carry it. That collapses the message join to two levels and the chat join to one. It is an
expensive migration on a large table; hold it in reserve.

### Write the multi-valued scopes as semi-joins, not paths plus `distinct()`

`experiments.participant` is scoped through `experimentsession__experiment__in`, which is
multi-valued and needs `.distinct()`. `SELECT DISTINCT … ORDER BY updated_at, id LIMIT 101` cannot
stop early: the whole join result is deduplicated before the limit applies, on every page.

```python
Q(pk__in=ExperimentSession.objects.filter(experiment_id__in=family).values("participant_id"))
```

is a semi-join — no duplicates, no `DISTINCT`, and the limit pushes down. Make that the rule for
`CHATBOT_PATH_REGISTRY`: forward FK chains as paths, everything else as `pk__in=<subquery>`. It
applies to `events.eventaction`'s three branches and the generic-FK models as well.

### The class (b) closure is re-evaluated on every page

`files.file`'s scope is a five-branch union, one branch of which (`chatattachment.files`) grows with
conversation volume. Every page of `files` rebuilds it.

The closure sets split into two groups that want different treatment. Experiments, pipelines, nodes,
collections, custom actions, providers, source material and consent forms are bounded: compute them
once per request as id lists, which also makes each dependent filter an indexed `IN`. The file
closure's attachment branch and the generic-FK scopes (`annotations.customtaggeditem`,
`annotations.usercomment`, `assessments.score`, built from `object_id__in=scope.chat_messages`) are
unbounded and have to stay subqueries, so budget one pass over the scoped messages per page of each
of those four resources. Page size is the main lever there.

Deriving the generic-FK filters from the same querysets that scope `chats`, `chat_messages` and
`sessions` is still the right call — `_resolve_generic_fks` aborts the import on any drift — it just
inherits their cost.

### A selection change costs a full re-import, not a full re-read

The cursor reset above is described as a correctness fix. Its cost lands on the target and is large.
`Importer._get_or_create` (`apps/teams/export/importer.py:340`) on an already-synced row runs
`.exists()`, `.get()`, `instance.save()` and then `_bypass_auto_now_update`'s second `UPDATE`, inside
a per-row `transaction.atomic()`, alongside a SQLite upsert that commits per row
(`apps/teams/export/translation.py:31`). That is roughly four statements and an fsync per row, so
re-reading millions of rows is hours of redo, not minutes.

Three mitigations, largely independent:

- Store the source `updated_at` next to the target key and skip a row whose timestamp is unchanged.
  A re-read becomes a dict lookup. This makes ordinary reruns proportional to changed rows rather
  than to all rows, so it is worth doing whether or not the selection feature lands.
- Reset only when the selection *grows*. Shrinking it leaves every cursor valid, since the source
  then serves a subset. Only additions need a reset, and only for the models the added chatbot
  populates.
- Set `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` on the store. One line; removes the
  per-row fsync while keeping crash consistency.

This is also the strongest argument for revisiting the rejected `?chatbot=` parameter. Validated
against the allowlist it keeps the authorization property — the client can only ask for what the
server already permits — and it buys per-chatbot cursors, so adding B never invalidates A, plus a
one-element `IN` list, which is the best input the planner can get for the join above. The "no query
params on ~60 endpoints" objection overstates the cost: `_QUERY_PARAMETERS` (`views.py:190`) is a
single list that `resource_view` applies to every resource, so it is one entry rather than sixty.

### The first run after upgrading re-imports everything

A state DB written by the previous release has no `cursors` rows and no stored source `updated_at`
values. The first `sync_team` run after the upgrade therefore reads every resource from the start
and cannot skip any row, so it costs the same as a sync into an empty target. The result is correct,
because the upserts are idempotent, and later runs skip unchanged rows as usual. Operators with a
sync in progress should plan for that one long run, or finish the sync on the old release first.

### `frozen_experiment_q()` lands on hot paths

`_get_static_triggers_to_fire` runs per conversation event and `enqueue_timed_out_events` runs every
ten seconds install-wide (`config/settings.py:611`). Both currently carry
`.exclude(experiment__team_id__in=migrating_team_ids())`: one cheap subquery over a small table.

The replacement must not put an M2M join, an `isnull=True` on a reverse relation and an OR of three
branches inside an `exclude()`. Django compiles a multi-valued `exclude()` to `NOT (pk IN
(subquery))`, which is both a planner trap and a semantics trap. Keep the predicate on plain indexed
columns:

```python
Q(experiment__team_id__in=<migrating teams with an empty selection>)
| Q(experiment_id__in=<expanded family ids for teams with a selection>)
```

Both sides are small — the second is bounded by the selected chatbots' version counts — and both can
stay lazy subqueries over simple columns. These run thousands of times a day while migration mode
changes on a human timescale, so a short cache is also reasonable.

### Client state scales with rows synced, not with chatbots

Two existing behaviours get worse as one store accumulates several chatbots:

- `FKTranslationStore.__init__` (`apps/teams/export/translation.py:25`) loads the whole
  `fk_translation` table into a Python dict. At millions of rows that is hundreds of MB resident on
  the sync host, and it grows across selections because the FK map is deliberately shared.
- `_start_cursor` (`apps/teams/management/commands/sync_team.py:90`) builds `pk__in=<every committed
  target pk for the model>`. Measured at 6.6s for 1M ids against an empty table — parameter
  marshalling alone — once per `updated_at_id` model per run.

The explicit `cursors` table removes the second outright. That is a second reason to keep it early in
the sequence: it is listed as the silent-corruption fix, and it is also the fix for a startup cost
that scales with the size of the migration.

### N+1 on the m2m fields in the export serializer

`build_resource_serializer` uses `fields = "__all__"`, so each m2m becomes a
`PrimaryKeyRelatedField(many=True)` that queries once per row. Measured: `chat_attachments` issues 3
queries for 2 rows and 13 for 12, so 101 per full page. `chat.chatattachment.files`,
`pipelines.node.collection_indexes` and `human_annotations.annotationqueue.assignees` are all
affected; `tags` on `Chat`/`ChatMessage` is not, because taggit's manager is not serialized and tags
travel as `custom_tagged_items`.

`PREFETCH_REGISTRY` (`apps/teams/export/manifest.py:193`) exists for exactly this and currently holds
one entry. Adding `prefetch_related("files")` and `prefetch_related("collection_indexes")` removes
the per-row queries.

### Load on the source

The export endpoints have no throttle, and the source is a live server still taking traffic while the
sync issues tens of thousands of queries against it. Check `statement_timeout` applies to them, and
point the sync at a read replica where the deployment has one.

## Suggested sequencing

0. Add the `(updated_at, id)` indexes and the `PREFETCH_REGISTRY` entries. Independent of the
   selection feature, improves the existing full-team sync on its own, and step 4 cannot be measured
   without it.
1. Fix `EventAction.params["pipeline_id"]` remapping — a standing bug in the full-team sync, and a
   prerequisite for the closure. Its own commit.
2. Fix the cursor-per-selection problem in the store: independent of everything else, and the only
   thing here that corrupts data silently. Carry the source `updated_at` per row and set the SQLite
   pragmas in the same pass, so a reset costs a re-read rather than a re-import.
3. Add `Team.exportable_experiments` and the migration-mode UI. The selection has to exist before
   anything can read it.
4. Add the scope plumbing end to end for class (a) only — chatbot, sessions, chats, messages,
   triggers. Testable on its own.
5. Add the class (b) closure.
6. Narrow the migration freeze, add the model exclusions, and update the sync report.
