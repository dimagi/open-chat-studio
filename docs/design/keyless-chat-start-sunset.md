---
status: active
---

# Closing the keyless `chat/start/` path

> Design document for the follow-up ADR-0053 named "the natural follow-up": withdrawing the anonymous,
> credential-free route into `POST /api/chat/start/`.
>
> Split out of [oauth-chat-widget.md](oauth-chat-widget.md), where it lived as D7. The two are
> independent: that document **adds** a credential (OAuth), this one **removes** a non-credential. They
> share only the admission table, and neither blocks the other.
>
> Builds on ADR-0034 (tiered feature deprecation gated by a usage audit), ADR-0036 (sunset headers for
> retired HTTP surfaces), ADR-0044 / ADR-0045 (durable per-channel widget auth policy and its ratchet),
> ADR-0041 (fail-closed session-token rollout) and **ADR-0053**, which deferred this deliberately.
>
> `status: active` — still evolving; ADR extraction is gated off until this flips to `stable`.

## TL;DR

A request to `POST /api/chat/start/` carrying **no credential at all** is still admitted. Anyone who
knows a chatbot's `public_id` — a value that ships in every embed snippet — can start a session on it.
ADR-0053 closed the neighbouring hole (a bare Django session cookie) but left this one open on purpose,
because widgets older than 0.5.1 have no embed key to send.

The published deprecation covering those widgets sunsets on **2026-10-01**. This document closes the
path once that date has passed **and** the remaining affected teams have been triaged.

Three things end together, because the last two are only reachable through the first:

| # | What ends | Where |
|---|---|---|
| 1 | Keyless `chat/start/` | `_check_start_session_access`, `apps/api/views/chat.py` |
| 2 | `WidgetAuthLevel.NONE` — "embed key optional", the pre-0.5.1 rung | `apps/channels/models.py` |
| 3 | The `is_public` / participant-allowlist fallback | `SessionAccessPermission._has_legacy_access`, `apps/api/permissions.py` |

Afterwards `_has_legacy_access` reduces to "a valid embed key for *this* session's channel (however the
request authenticated), and the channel is not `SESSION_TOKEN`" — a real simplification of the
trickiest permission code in the app.

Four decisions shape the work:

1. **The date is a checkpoint, not the trigger** — enforcement needs the date *and* a deliberate
   switch-on, per ADR-0034. (See [D1](#d1-the-date-is-a-checkpoint-not-the-trigger).)
2. **The affected population is made queryable first**, via a session-metadata marker; logs alone
   cannot drive a notification or a triage decision. (See [D2](#d2-make-the-population-queryable).)
3. **Integrators are told in-band and out-of-band** — RFC 8594 headers on every keyless response, plus
   an escalating notification to the admins who can actually fix it. (See
   [D3](#d3-telling-people-before-it-breaks).)
4. **ADR-0053's second precondition is satisfied before the gate can open**, not after. (See
   [D4](#d4-satisfying-the-ratchet-precondition-first).)

## What is open today

`chat_start_session`'s only permission class is `WidgetDomainPermission`, which returns `True` whenever
`request.auth` is not an `ExperimentChannel` — so an anonymous request with no headers passes it.
`_check_start_session_access` then returns `None` for any unauthenticated caller that did not ask for a
specific `version_number`:

```python
if not request.user.is_authenticated:
    # Otherwise unaffected: the permission classes are the only gate on the anonymous path.
    if version_number is None:
        return None
```

ADR-0053 was explicit that this is deferred rather than accepted:

> **This binds session-authenticated callers only. Anonymous callers are not required to present an
> embed key.** […] What is being withdrawn is a privilege the Django session cookie was granting on its
> own — nothing is being added to the anonymous path.

and named the two preconditions for closing it: the 0.6.0 sunset date passing, and the ADR-0045 ratchet
having moved live channels off `NONE`.

**Why the wait is real.** Widgets below 0.5.1 predate the embed key entirely (`EMBED_KEY_INTRODUCED` in
`apps/channels/widget_versions.py`) and cannot send one. Closing the path today breaks every such embed
on deploy, and the fix is not one an OCS team admin can apply — it means editing the embed snippet on
someone else's website.

## Decisions

### D1: The date is a checkpoint, not the trigger

The obvious implementation is self-executing: a `KEYLESS_CHAT_START_SUNSET_AT` constant and a
`timezone.now() >= …` check. Production then changes behaviour at 00:00 UTC with no deploy, no
recourse, and no one necessarily watching. That is the "hard removal date" ADR-0034 lists under
rejected alternatives, and the "hard cutover with no opt-out" ADR-0041 rejected for the closely related
session-token rollout. ADR-0034 is the repo's deprecation process, and it applies here:

> Removal requires **both** the date passing **and** every remaining active team triaged — contacted,
> migrated, or breakage explicitly accepted by the feature owner.

So enforcement requires two things, in this order:

1. **The date has passed** — `KEYLESS_CHAT_START_SUNSET_AT` alongside the existing `EMBED_FLOW_REMOVED_ON` (a plain `date`, where this one is tz-aware)
   in `apps/experiments/const.py`, testable with `time_machine.travel`, which the suite already uses.
2. **A deliberate switch-on**, after the owner has triaged the teams still producing `KEYLESS_START`
   markers ([D2](#d2-make-the-population-queryable)). Flipping it back is the kill switch.

The published 2026-10-01 date does not move and is still what integrators are told; it simply stops
being the thing that pulls the trigger. This is a **single global gate**, not a per-team one — one
deadline and one decision.

**Rejected: grandfathering chatbots with recent keyless traffic.** Auto-creating an enabling record for
active traffic would create exactly the exposure the change exists to close, for exactly the chatbots
that are most exposed.

**Rejected: a per-team waffle flag.** Distinct from the single global gate above. A published
deprecation with a fixed date is already in flight; a *per-team* flag would turn one announced deadline
into an open-ended negotiation repeated per team, which is the "single holdout stalls removal
indefinitely" failure ADR-0034 explicitly guards against. A flag that *forces strictness early* for a
team that asks is a cheap addition if wanted.

### D2: Make the population queryable

A triage gate is only affordable if "who is still doing this" is a query rather than an investigation.

- Every keyless start is **marked**: a `Chat.MetadataKeys.KEYLESS_START` flag in the session metadata,
  beside the existing `EMBED_SOURCE` referer capture (now passed through `safe_link_url`).
- It is also **logged**, structured, on `ocs.api_chat`.

The marker is the load-bearing half. Logs age out, are not joinable to teams and channels, and cannot
drive a periodic notification task; a metadata flag on the session can do all three. The log line is
for debugging a specific request, not for measuring the population.

This ships **first and alone** — it is inert, has no user-visible effect, and the longer it runs before
the gate opens the better the triage data.

### D3: Telling people before it breaks

**In-band.** Keyless responses carry RFC 8594 headers — `Deprecation: true`, `Sunset: <2026-10-01>`,
`Link: <docs>; rel="successor-version"` — via a conditional helper in the shape of
`apply_widget_sunset_headers`, so an integrator reading responses sees the deadline without being told.
This follows ADR-0036, which already established sunset headers as how OCS retires an HTTP surface.

**Out-of-band.** A periodic task notifies affected teams, following the existing
`widget_auth_level_upgrade_notification` pattern in `apps/channels/tasks.py`. Two details matter:

**Escalating cadence.** One notice on first detection, then at T-90, T-60, T-30, T-14, T-7 and T-1.
Each milestone carries its own `event_data={"milestone": "T-30"}` so it opens a fresh notification
thread rather than being deduplicated against the last one — the same trick `min_version` plays for the
ratchet notification.

**Milestones already past when the task first runs are not emitted.** Phase 1 ships after T-90
(2026-07-03) and T-60 (2026-08-02) have gone by, and a team that has never been told anything is not
helped by three notices in one day. The first-detection notice carries the sunset date itself, so it
already says everything the missed milestones would have; the task then resumes at the next *future*
milestone. This has to be stated rather than left to the implementation, because the obvious loop —
"emit every milestone whose date has passed and that has no thread yet" — produces exactly the burst
this avoids. Worth a test that rolls out after a milestone and asserts a single notice.

**Admins, not every member.** The framework targets by permission
(`get_users_to_be_notified(team, permissions)`), so the audience is
`["bot_channels.add_experimentchannel"]` — the Chatbot Admin and Super Admin groups, the people who can
actually create the channel that fixes it. Note that `has_perms` is an **AND**: adding
`oauth.add_oauth2application` would narrow the audience to users in *both* groups, so where the fix
involves an OAuth application (registered by a Team Admin, who holds `oauth.*` but *not*
`bot_channels.*`) it is named in the message body and linked instead.

### D4: Satisfying the ratchet precondition first

ADR-0053's second precondition is that the ADR-0045 ratchet has moved live channels off
`WidgetAuthLevel.NONE`. Two things serve it, in this order: the pre-cutover notification reports the
channels still sitting at `NONE` so they can be triaged, and a data migration then raises whatever
survives that triage to `EMBED_KEY`.

**It ships in Phase 2, before the gate opens — not with the Phase 1 instrumentation.** Two things rule
Phase 1 out, and they are the same thing seen from either end. A channel sits at `NONE` precisely
because the widget it last saw was below 0.5.1 (`level_for_version`: unparseable or `< 0.5.1` → `NONE`),
so it is the one population that *cannot* satisfy `EMBED_KEY` — raising it is not a formality, it is the
breakage this document exists to schedule, arriving early and without notice. And Phase 1 asks the
notification to report "channels still at `NONE`", which a migration in the same phase empties: the
report and the migration cannot both be Phase 1 or the report has nothing to describe.

So Phase 1 only *observes* the `NONE` population and names it in the notice, and the migration runs in
Phase 2 as the last act before the gate opens — after triage has decided what to do about each survivor.
That still satisfies the ordering an earlier draft got wrong: the precondition is enforced before the
thing it gates, rather than after the date has already fired. What it gives up is being able to answer
"is the precondition met?" with a migration that has already run; instead triage answers it by looking at
the reported population, which is the more honest reading of ADR-0053's precondition anyway — it asks
that live channels have *moved off* `NONE`, not that a migration has overwritten the column.

## Implementation outline

**Phase 1 — instrument and prepare (ships now, inert)**

| # | File | Change |
|---|---|---|
| 1 | `apps/chat/models.py`, `apps/api/views/chat.py` | `KEYLESS_START` metadata marker + structured log (D2). |
| 2 | `apps/api/views/chat.py` | RFC 8594 headers on keyless responses (D3). |
| 3 | `apps/experiments/const.py` | `KEYLESS_CHAT_START_SUNSET_AT = datetime(2026, 10, 1, tzinfo=UTC)`; the triage gate, defaulting to off (D1). |
| 4 | `apps/channels/tasks.py` | Escalating notification to affected teams, reporting channels still at `NONE` (D3). |

**Phase 2 — after the date, and after triage**

| # | Change |
|---|---|
| 5 | Triage the teams still producing `KEYLESS_START` markers, and the channels still at `NONE` (ADR-0034: contacted, migrated, or breakage accepted by the owner). |
| 6 | `apps/channels/migrations/`: data migration `WidgetAuthLevel.NONE` → `EMBED_KEY` (D4) — after triage, before the gate opens. |
| 7 | Open the gate. |
| 8 | Delete the keyless branch in `_check_start_session_access`, the `NONE` handling, and the `is_public` fallback in `_has_legacy_access`; drop the date check and the gate. |

No database migration in Phase 1 at all — the marker lives in existing session metadata and the gate is
a settings value.

**ADRs to extract** when this flips to `stable`: the cutover and its gate, which supersedes the
"deferred, not rejected" clause of ADR-0053 and should cite ADR-0034 for the gate's shape.

## Test plan

| Case | Expected |
|---|---|
| No credential, gate closed | `201` + `Deprecation`/`Sunset`/`Link` headers + `KEYLESS_START` marker |
| No credential, date passed, gate still closed (`time_machine`) | `201` — the date alone does not enforce (D1) |
| No credential, date passed **and** gate open (`time_machine`) | `401` |
| No credential, gate open but date **not** passed | `201` — both conditions required, in order |
| Credentialed request, gate closed | no `KEYLESS_START` marker, no sunset headers |
| Channel at `NONE` after the Phase 2 data migration | raised to `EMBED_KEY` |
| Channel at `NONE` during Phase 1 | left at `NONE`, and named in the notification |
| Notification task first run after a milestone has passed | one notice, not one per missed milestone (D3) |
| Notification task | one thread per milestone (not deduplicated); audience is `bot_channels.add_experimentchannel` holders only |

Regression guards: `test_chat_api_anon.py` (updated for the marker and headers),
`test_widget_auth_level.py`, `test_chat_session_token.py` and `test_embedded_widget_auth.py` stay green.
After Phase 2, a test asserting `_has_legacy_access` no longer consults `is_public`.

## Deliberately out of scope

- **The OAuth credential and the Chat API Channel's credential mode** — [oauth-chat-widget.md](oauth-chat-widget.md).
- **Retiring `is_public` as an `Experiment` field.** Phase 2 removes the last chat-API path that reads
  it; other surfaces may still. Dropping the column is its own deprecation under ADR-0034.
- **The non-API chat surfaces** (Django web-chat views, messaging platforms) — untouched.

## Open questions

1. **Who owns the triage gate, and what evidence opens it?** ADR-0034 requires a named owner to accept
   the remaining breakage. The `KEYLESS_START` query supplies the population; the sign-off is a process
   decision, not a code one.
2. **How long should Phase 1 run before the gate is considered?** Longer means better triage data and
   more notification milestones actually delivered. The T-90 milestone implies at least a quarter.
