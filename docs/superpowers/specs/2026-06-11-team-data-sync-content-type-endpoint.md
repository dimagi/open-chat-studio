---
status: active
---

# Team Data Sync — Content-Type Slug Endpoint

An alternative to the five-endpoint design in `2026-06-08-team-data-sync-overview.md` and its
companion `2026-06-09-team-data-sync-read-api-design.md`. Same migration behavior (FK remap,
timestamp write-back, secret re-encryption, keyset pagination, per-team gate); only the API
shape changes.

## Core idea

One paginated endpoint, addressed by the model's content type:

```
GET /api/v2/sync/<app_label.model>/?cursor=<keyset>&limit=<n>[&public_key=<base64>]
```

It resolves the content type and returns that model's team-scoped rows, paginated. Each response
holds **one model's rows only** — nothing nested or bundled. The order to call the slugs in lives in
a manifest (below), not in the responses.

Models that aren't directly team-scoped (`BaseModel` / plain `Model` — e.g. `chat.*`,
`evaluations.evaluationmessage`, `evaluations.evaluationrunaggregate`, `analysis.analysisquery`) are
filtered to the team through a parent FK path; the endpoint resolves that path per slug.

## Manifest endpoint

`GET /api/v2/sync/manifest/` returns the ordered list of content types to pull, with the per-type
config the caller needs:

```json
{
  "entries": [
    { "slug": "teams.team",                   "phase": "structural", "cursor": "pk",           "secret": false },
    { "slug": "service_providers.llmprovider", "phase": "structural", "cursor": "pk",           "secret": true },
    { "slug": "experiments.experiment",        "phase": "structural", "cursor": "pk",           "secret": false, "order_by": "working_version_id_nulls_first" },
    { "slug": "documents.collectionfile",      "phase": "structural", "cursor": "pk",           "secret": false, "through": true },
    { "slug": "experiments.participant",       "phase": "live",       "cursor": "updated_at_id", "secret": true },
    { "slug": "chat.chatmessage",              "phase": "live",       "cursor": "pk",           "secret": false }
  ]
}
```

- The caller reads the manifest and goes through the entries in order — no hardcoded order in the command.
- The manifest is also the **allowlist**: the slug endpoint refuses any content type not in it, so
  the generic endpoint can't expose a model it shouldn't.
- Per-entry config drives the caller generically: `phase` (pull once vs poll until cutover),
  `cursor` (`pk`, or `updated_at_id`), `secret` (pass `public_key`, expect a re-encrypted value),
  `order_by`, `through`.

No exclusion list is served. A **test** enforces completeness instead:

- list every model (`apps.get_models(include_auto_created=True)`),
- subtract the manifest's slugs,
- assert the rest equals a `KNOWN_EXCLUSIONS` set declared in the test.

A new, unclassified model fails the test. Same idea as
`apps/teams/tests/test_permissions.py::test_missing_content_types`. Exclusion reasons live as
comments next to that set, not in the API response.

## One content type per call

- Each response holds one model's rows. No nesting.
- **Bare M2M** (no extra columns, e.g. `ChatAttachment`↔`File`, `EvaluationDataset`↔`EvaluationMessage`)
  are carried as a remapped id list on the owning row; the caller applies `.set()`. No separate call,
  which also avoids the fact that auto-created M2M tables may have no content type to address.
- **Through models with extra columns** (`documents.collectionfile`: status / external_id /
  metadata) get their own call, so the extra data and the right create method are preserved.
- **Trivial link rows the caller can rebuild** need no call — though `teams.membership` carries
  `role`/`groups`, so it keeps its own slug.

## Serialization

Each content type's rows are produced by a **dynamically built DRF `ModelSerializer`** — one per
model from a factory, not 62 hand-written classes — so a serializer can't drift from its model and a
new field is exported the moment it's added. The serializers are output-only; `.save()` is never
called.

The factory leans on `ModelSerializer`'s defaults rather than fighting them:

- The row's own primary key stays `id`.
- A **foreign key** serializes under its relation name with the pk as the value — DRF's default
  (`consent_form`, not `consent_form_id`). Those pks are source-env values the importer remaps via
  `FKTranslation`. (Naming therefore differs from the `*_id` shape in the read-api companion doc —
  this design keeps DRF's convention rather than interfering with it.)
- A **bare M2M** serializes to a pk list under its relation name — exactly the id list the caller
  feeds to `.set()`. M2M that must not be carried (group/permission membership) are dropped via the
  exclude registry below.
- Datetimes, JSON, and Decimals get DRF's standard coercion.

Two registries, co-located with the manifest and maintained by code review, are the only per-model
surface:

- `EXCLUDE_REGISTRY` — model → fields to drop (`customuser.password`, `widget_version*` telemetry,
  soft-delete columns, excluded M2M). Passed as `Meta.exclude`. "Dump every field" is the default;
  this is the small, explicit set of exceptions.
- `SECRET_REGISTRY` — model → field names that must be encrypted in transit.

Secrets are sealed by a single mixin shared across every generated serializer:

```python
class _SyncSecretMixin:
    def to_representation(self, instance):
        data = super().to_representation(instance)
        for field in self.secret_fields:
            data[field] = seal(getattr(instance, field), self.context["public_key"])
        return data
```

`seal` is the envelope encryption from the read-api companion's
[Secrets](2026-06-09-team-data-sync-read-api-design.md) section (a random symmetric key encrypts the
value; the target's RSA `public_key` wraps it). Because `encrypt()` (django-cryptography) decrypts
transparently on read, encrypted-at-rest fields (`provider.config`,
`participantdata.data`/`encryption_key`) and plaintext-sensitive fields
(`experimentchannel.extra_data`, `documentsource.config`) both reach `to_representation` as plain
Python values and take the identical path. `public_key` rides in the serializer context from the
endpoint's query param; non-secret models never read it.

The manifest entry's `secret` flag is **derived** from `SECRET_REGISTRY` membership, not declared
twice — a test asserts the two agree so they can't drift.

A few models need a value that isn't a plain field dump — `teams.team.feature_flags` (flag names),
`teams.membership.groups` (group names), the `is_global` flag on matched global rows. These are the
only genuinely per-model code: each is declared as a `SerializerMethodField` and passed to the
factory as an extra field.

**Module layout:** `apps/api/v2/sync/serializers.py` holds the factory and `_SyncSecretMixin`;
`apps/api/v2/sync/manifest.py` holds the manifest entries, `SECRET_REGISTRY`, and `EXCLUDE_REGISTRY`
— a single maintenance surface for the whole endpoint.

## Versions (working before published)

Pull `experiments.experiment` and `pipelines.pipeline` ordered by
`working_version_id NULLS FIRST, id`. Working versions have a null `working_version_id`, so every
working version comes before any published one across the whole stream — the working version always
exists before a published version references it. No separate version manifest needed.

## Model classification

Every registered model is here: a **synced** row (a manifest entry) or **excluded** (with a reason
that lives in the test's `KNOWN_EXCLUSIONS`). Cursor rule: structural data is pulled once from an
unchanging source, so `pk`; live append-only is `pk`; live mutable uses `updated_at_id` so edits to
already-synced rows are re-pulled during the polling window.

### Synced (manifest entries, in call order)

Numbered 1–62; the **Excluded** list below continues 63–134. One continuous run over every
registered model (`apps.get_models(include_auto_created=True)` = 134), so a reader can confirm
nothing is unaccounted for.

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
| 19 | `files.file` | structural + re-poll live | pk | | one slug for all files; pulled early (collection files), re-polled live (attachment files); content fetched separately |
| 20 | `documents.collectionfile` | structural | pk | | through (status/external_id/metadata); after collection + files |
| 21 | `documents.documentsource` | structural | pk | ✓ | `config` (plaintext-sensitive, re-encrypted) |
| 22 | `files.filechunkembedding` | structural | pk | | bulk vectors; after files + collections |
| 23 | `ocs_notifications.eventtype` | structural | pk | | |
| 24 | `ocs_notifications.usernotificationpreferences` | structural | pk | | refs user |
| 25 | `pipelines.pipeline` | structural | pk | | `order_by` working-first; edges in graph |
| 26 | `pipelines.node` | structural | pk | | |
| 27 | `experiments.experiment` | structural | pk | | chatbots; `order_by` working-first |
| 28 | `bot_channels.experimentchannel` | structural | pk | ✓ | `extra_data` (plaintext-sensitive); webhook re-registration manual |
| 29 | `events.eventaction` | structural | pk | | referenced by triggers + scheduled messages |
| 30 | `events.statictrigger` | structural | pk | | |
| 31 | `events.timeouttrigger` | structural | pk | | keeps `config_changed_at` |
| 32 | `experiments.participant` | live | updated_at_id | | |
| 33 | `experiments.participantdata` | live | updated_at_id | ✓ | `data` + `encryption_key` encrypted |
| 34 | `chat.chat` | live | pk | | before session (`ExperimentSession.chat` is a OneToOne to `Chat`); created-once → pk |
| 35 | `experiments.experimentsession` | live | updated_at_id | | refs experiment / participant / chat / channel |
| 36 | `chat.chatattachment` | live | pk | | carries remapped `file_ids` (bare M2M) |
| 37 | `chat.chatmessage` | live | pk | | append-only |
| 38 | `trace.trace` | live | pk | | append-only |
| 39 | `pipelines.pipelinechathistory` | live | updated_at_id | | |
| 40 | `pipelines.pipelinechatmessages` | live | pk | | append-only |
| 41 | `events.scheduledmessage` | live | updated_at_id | | refs participant / experiment / eventaction |
| 42 | `ocs_notifications.notificationevent` | live | pk | | append-only |
| 43 | `ocs_notifications.eventuser` | live | updated_at_id | | inbox read-state |
| 44 | `evaluations.evaluator` | live | updated_at_id | | judge config |
| 45 | `evaluations.evaluationmessage` | live | updated_at_id | | `BaseModel` (team via parent); refs `chatmessage` + `session` |
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

### Excluded (`KNOWN_EXCLUSIONS`, grouped by reason)

Numbering continues from the manifest: 63–134. Every group is fully enumerated (no `*` wildcards or
collapsed members) so the run is gapless — 1–134 covers all 134 registered models exactly once.

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
- **Auto-created M2M through tables** (the guard sees these via `include_auto_created=True`). Bare
  M2M are carried as id-list fields on their owning synced row, so they need no slug; the rest are
  excluded outright — group/permission membership is re-established on the target:
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

## Cutover

Pull everything first, then switch over once. Channel webhooks are re-registered with the external
platforms in a single pass at the end — not per-bot. The command emits a **manual checklist** for
anything that can't be re-established automatically, so nothing is silently dropped:

- channel webhooks that need re-pointing at the new server,
- `slack.*` installs to re-add,
- the auth/identity items in `KNOWN_EXCLUSIONS` that re-establish on the target — `oauth.*`
  applications, social login, MFA re-enrollment, re-issued API keys.

## Pros vs the five-endpoint design

- No unbounded payload — everything is paginated (the original's `export-team` is one large response).
- Best coverage and testability — the guard test maps one-to-one to the manifest; nothing is hidden in a bundle.
- Simple to follow and audit — one call, one model.
- One `files` slug, polled in both phases — no files/attachment-files split.
- Server owns the order (manifest), so the command is generic and a newer source can add models
  without a command change.
- The manifest is the allowlist — removes the risk of the generic endpoint exposing too much.

## Cons vs the five-endpoint design

- New pattern — v2 today is router + ViewSets; a content-type dispatch view with per-type
  cursor/secret config and strict team-scoping is new to the codebase.
- Many ordered calls vs a few endpoints — more round-trips, though bounded by pagination.
- No atomic bundles — a chatbot, its channels, and its events arrive in separate calls, so a
  half-applied chatbot is a transient state between calls (checkpoint/resume covers it).
- The manifest only declares what/order; the caller still owns the how (FK remap, secrets, through
  handling). Fine while the flag set stays small — a warning sign if every model needs a special flag.
