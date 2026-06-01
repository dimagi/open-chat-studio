# ADR-0018: Scope team querysets by FK identity, not slug join

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>

## Context

Most resources are team-scoped (`BaseTeamModel`). Authenticated views resolve `request.team` through middleware, lazily, from the `team_slug` URL kwarg, so the current team is already in scope before any view body runs (enforced by `@login_and_team_required` / `LoginAndTeamRequiredMixin`).

Despite this, many querysets filtered with `team__slug=<slug>`. That lookup traverses the `team` FK into `teams_team` purely to match a slug — forcing a JOIN to resolve a team id the request already held. It read as the natural form because the slug is the URL kwarg, but the join is redundant wherever `request.team` exists.

## Decision

We will scope team-bound querysets in authenticated views by the `team` FK identity — `team=request.team` — rather than the slug traversal `team__slug=<slug>`, wherever `request.team` is in scope.

## Consequences

- Each converted lookup drops a JOIN to `teams_team` from the emitted SQL; the win is most visible on hot API list endpoints that run on every request.
- New code has one idiomatic scoping form to copy, and a `team__slug=` filter in a request-scoped view becomes a reviewable regression signal.
- No behaviour change — both forms resolve to the same team and return identical rows.
- The convention applies only where `request.team` exists; code without a request in scope (models, management commands, and views working off a raw slug) continues to filter by `team__slug=`.

## Alternatives considered

- Keep `team__slug=` everywhere → rejected: pays for a redundant JOIN, and mixing two scoping idioms invites drift.
- `team_id=request.team.id` → rejected: emits equivalent SQL but reads less clearly than passing the instance, with no offsetting benefit.
