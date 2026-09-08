# ADR-0058: Tri-state `everyone` and team grants are a flag's only inputs

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Barry Tandy · Created: 2026-09-03</p>

## Context

`apps.teams.models.Flag` subclasses waffle's `AbstractUserFlag` and adds a `teams` M2M, so a flag gates a feature per team. `is_active_for_team` decided a flag by team membership and did not read `everyone`.

Waffle's remaining inputs resolve against an HTTP request: `_is_active_for_user` reads `request.user`, `_is_active_for_language` reads `request.LANGUAGE_CODE`, and `_is_active_for_percent` reads `request.COOKIES`. That covers `superusers`, `staff`, `authenticated`, `testing`, `rollout`, `percent`, `languages`, and the `users` and `groups` M2Ms. Team-scoped callers in `apps/pipelines/views.py`, `apps/chatbots/views.py`, `apps/teams/views/feature_flags.py`, `apps/documents/models.py` and `apps/channels/models.py` reach a flag with a team and no request, where those inputs are set in the admin and decide nothing.

Three effects followed, recorded on #4321. `Collection._flag_active_for_team` restored waffle's precedence locally, so a flag with `everyone=True` was on for hybrid search and off for the channel platform dropdown at once. `waffle.testutils.override_flag` sets `everyone` alone, so it did not reach team-scoped flags and six hand-rolled fixtures stood in for it. Every writer stored `everyone=False` to mean "no global override, use teams" - the team settings screen, the `CREATE_MISSING_FLAGS` path, migration `teams/0010`, and `FlagUpdateForm`'s plain `BooleanField` - which left no way to express a hard off and made `None` unreachable through `/admin/flags`.

Waffle's `superusers` column defaults to `True`, so rows carried that value without it being chosen.

## Decision

We will make a flag's answer come from `everyone` and `teams` alone, on every path. Shipped against #4321 as #4359, then #4369 with #4370 merged into it.

- `everyone` is a tri-state read by `is_active_for_team`: `True` is on for every team, `False` is off for every team, `None` defers to the `teams` M2M. `Collection._flag_active_for_team` is deleted and its callers use `is_active_for_team`.
- The request-only inputs and the `users` and `groups` M2Ms leave `FlagUpdateForm`, the flag templates and `FlagAdmin`. Migration `teams/0016` resets their stored values to inert defaults, clears the two M2Ms, and converts stored `everyone=False` to `None`.
- The columns stay on the table, since they come from `AbstractUserFlag`.
- Rows minted by `WAFFLE_CREATE_MISSING_FLAGS` are created with `everyone=None` and `superusers=False`.
- Surfaces that need superusers to always see a feature gate on `is_superuser` rather than on a flag.

## Consequences

- A flag answers the same with or without a request in scope. `override_flag` now reaches team-scoped flags, and the hand-rolled fixtures give way to `team_flag` in `apps/conftest.py`.
- "Off for everyone" becomes expressible. Stored `False` converts to `None`, so existing rows keep deferring to their teams.
- Superusers lose blanket access to flagged features on request paths.
- The neutralisation has to precede the `everyone` conversion. Converting first would let waffle reach the `superusers=True` check and turn every flag on for superusers on request paths.
- The team settings screen initialises its checkboxes from `is_active_for_team`, so a globally-on flag shows ticked for every team and unticking it does nothing.
- Migration `teams/0016` clears user and group grants behind a noop reverse.
- The neutralised columns stay writable outside the admin. `FLAG_FIELDS` audits all of them, so such a write is recorded.
- Whether to override the `superusers` field default on the model, which would also cover rows added through the Django admin add form, is unresolved.

## Alternatives considered

- **Treat the omission of `everyone` from `is_active_for_team` as deliberate**, documenting it and giving tests a shared team-flag fixture - rejected: it leaves `Collection._flag_active_for_team` as a second implementation that disagrees with the first.
- **Keep the request-only inputs and support them off-request** - rejected: they need the user, language and cookies that only a request carries.
- **Drop the unused columns from the table** - rejected: they come from `AbstractUserFlag`.
