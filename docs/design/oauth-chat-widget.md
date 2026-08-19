---
status: active
---

# Chat API admission model: OAuth, embed keys, and deny-by-default

> Design document for [#3893](https://github.com/dimagi/open-chat-studio/issues/3893) — OAuth for the
> chat widget and chat API — widened into the access model the chat API needs as a whole.
>
> Builds on [#3710](https://github.com/dimagi/open-chat-studio/issues/3710) (client-credentials OAuth
> applications pinned to a team), [#4197](https://github.com/dimagi/open-chat-studio/issues/4197) /
> [PR #4198](https://github.com/dimagi/open-chat-studio/pull/4198) (those applications pinned to
> specific chatbots), and on ADR-0039 (proof of possession for session access), ADR-0054 (session
> tokens expire on absolute age, superseding ADR-0040's expiry rule), ADR-0044 / ADR-0045 (durable
> per-channel widget auth policy and its ratchet), ADR-0052 (app-layer rate limiting) and **ADR-0053**
> (starting a session requires team membership or the embed key), all merged.
>
> The anonymous keyless path is **not** covered here: closing it is a removal rather than an addition,
> runs on a published deprecation clock, and is specified in
> [keyless-chat-start-sunset.md](keyless-chat-start-sunset.md). The two are independent and neither
> blocks the other.
>
> `status: active` — still evolving; ADR extraction is gated off until this flips to `stable`.

## TL;DR

**A chatbot is unreachable over the chat API until an admin exposes it.** Exposure is a single record
— the **Chat API Channel** (`ChannelPlatform.EMBEDDED_WIDGET`, relabelled *Chat Widget & API*) — and
that channel's **credential mode** says what a caller must present: the embed key, or an OAuth token.
Nothing else gets in.

ADR-0053 already closed the cookie hole and built the authorization site this work extends. Two things
are left to reach deny-by-default: **add the OAuth credential** as a third way in — this document — and
**close the anonymous keyless path**, which ADR-0053 deferred to the 2026-10-01 sunset and which
[keyless-chat-start-sunset.md](keyless-chat-start-sunset.md) now owns.

**Until that second piece lands, deny-by-default is the destination, not the state.** A caller with no
credential at all is still admitted. Nothing in this document changes that either way; it is called out
in the table below rather than quietly assumed away.

| Credential on `chat/start/` | What must be enabled | Session's channel | Status |
|---|---|---|---|
| `X-Embed-Key` | a Chat API Channel in `embed_key` mode (the key *is* that channel) | that channel | shipped |
| Django session **+ team membership** | nothing — in-app surfaces | the team's API entry point (the `API` pseudo-platform row, *not* a Chat API Channel) | shipped (ADR-0053) |
| Django session **+ embed key** | a Chat API Channel | that channel | shipped (ADR-0053) |
| `Authorization: Bearer` (any `X-Embed-Key` ignored) | a Chat API Channel in `oauth` mode **and** the chatbot listed on the token's OAuth application | that channel | **this work** |
| *(none)* | — | — | still admitted — closed by [keyless-chat-start-sunset.md](keyless-chat-start-sunset.md) |

Admission is decided when a **session is created**. Everything after that is ADR-0039's
proof-of-possession, unchanged.

Seven decisions shape the work:

1. **Enablement is the Chat API Channel, and its credential mode says who gets in.** No new platform:
   a `credential_mode` column on the existing channel, which also pins the widget auth level. (See
   [D1](#d1-the-chat-api-channel-and-its-credential-mode).)
2. **Each credential validates its own origin.** A token-only caller is admitted without an
   `Origin`; an embed key without one is still refused. (See
   [D2](#d2-the-origin-rule-follows-the-credential).)
3. **The OAuth branch extends ADR-0053's authorization helper** rather than adding a parallel gate;
   an authentication class first in the list resolves the credential, which also fixes the rate
   limiter's identity for free. (See [D3](#d3-extend-the-existing-authorization-site).)
4. **Client-credentials tokens only**, carrying a new narrow scope `chat:start` — *not*
   `chatbots:interact` — with token team = chatbot team, **and the token's application explicitly
   listing this chatbot**. (See [D4](#d4-what-makes-a-token-acceptable).)
5. **Session-bound endpoints are untouched.** ADR-0039 already settled this. (See
   [D5](#d5-session-bound-endpoints-are-untouched).)
6. **`401` falls out of the authenticator's position**, not a workaround; the exception survives only
   to carry the response body. (See [D6](#d6-getting-a-401-out-of-drf).)
7. **Admission is bounded in time.** An absolute session lifetime **replaced** ADR-0040's sliding
   inactivity window — the two cannot usefully coexist — so a session cannot be kept alive
   indefinitely and the OAuth gate is re-crossed rather than passed once. Shipped as **ADR-0054**;
   the per-channel override is what remains. (See [D7](#d7-admission-is-bounded-in-time).)

## What is already in place

### ADR-0053 (PR #4178, merged 2026-08-12)

This landed while the document was in review and did a large part of what earlier drafts proposed:

- **The cookie hole is closed.** A session-authenticated caller must belong to the chatbot's team
  **or** present that chatbot's embed key. Being logged in is identity, not access.
- **`_check_start_session_access(request, experiment, embed_key_channel, version_number)`** in
  `apps/api/views/chat.py` is now the single authorization site for `chat_start_session`, returning a
  `403` `Response` or `None`. Version selection stayed a member-only capability.
- **`embed_key_authorizes_channel(request, channel)`** and **`get_embed_key_channel(request, experiment)`**
  (`apps/api/authentication.py`) validate an embed key *and its origin* together, callable from
  anywhere. They exist because DRF stops at the first matching authenticator: `SessionAuthentication`
  precedes `EmbeddedWidgetAuthentication`, so a same-origin widget's cookie always wins and
  `request.auth` is never the channel. `SessionAccessPermission` calls the same helper.
- **Attribution tracks the widget, not the authenticator.** `_resolve_experiment_channel` takes the
  resolved `embed_key_channel` and gives the session to it whenever the key is present, however the
  request authenticated.
- **`ChatWidgetConfig` derives the embed key** from its configured `chatbot_id`
  (`get_widget_embed_key`, cached, with `clear_widget_embed_key_cache` on save) rather than storing a
  copy — an earlier draft of this document proposed storing one, which ADR-0053 rejected as a value
  that goes silently stale on rotation. The admin form refuses a chatbot with no widget channel.
- **Deleting a widget channel revokes its embed key**: `embed_key_authorizes_channel` rejects
  soft-deleted channels, which FK traversal (`session.experiment_channel`) would otherwise hand back.

Three consequences of ADR-0053 that this design now inherits rather than argues for: the site help
widget is an ordinary embedded widget (so widget telemetry and the ADR-0045 ratchet see it), one
widget yields one channel and one participant regardless of viewer, and **the anonymous keyless path
is explicitly still open** — ADR-0053 names closing it as "the natural follow-up", gated on the
0.6.0 sunset of 2026-10-01 *and* on the ratchet having moved live channels off `NONE`.

### ADR-0052 rate limiting

`ChatAPIRateThrottle` (`apps/api/throttling.py`) now runs on all five chat endpoints, bucketing per
conversation: `session_id` when there is one, else the `ExperimentChannel` in `request.auth`, else
the client IP. **An OAuth caller would fall to the IP bucket**, so every machine caller behind one
egress IP would share an allowance — and share it with unrelated anonymous traffic from that IP.
[D3](#d3-extend-the-existing-authorization-site) closes this without touching the throttle: resolving
the credential in an authentication class puts the channel in `request.auth`, which the existing
`ExperimentChannel` branch already buckets on.

### The per-application chatbot allowlist (ADR-0055, PR #4198, merged 2026-08-14)

What [D4](#d4-what-makes-a-token-acceptable) called a prerequisite has shipped, closing #4197 and
recorded as **ADR-0055** (*Client-credentials applications name the chatbots they may reach*):

- **`OAuth2Application.allowed_chatbots`** — M2M to `Experiment`, `related_name="oauth_applications"`,
  audited via `@audit_fields`, migration `oauth.0004`. **Empty means none.**
- **`application_allows_chatbot(request, experiment)`** and
  **`enforce_application_chatbot_access(request, experiment)`** in `apps/oauth/permissions.py`. The
  first returns `True` for every non-client-credentials caller, so API-key, Django-session and
  authorization-code semantics are untouched; the second raises `PermissionDenied` (`403`).
- **It already gates `chatbots:interact`** at `_chat_completions`, `_new_api_message` and
  `handle_trigger_bot_message` — six views — so the inconsistency D4 objected to (one scope meaning
  different things at different doors) is gone before this work starts.
- **The allowlist holds working versions**, and the check normalises the caller's chatbot through
  `get_working_version_id()`, because `public_id` is unique per row and a caller may legitimately
  address a version directly.
- The registration/edit form renders a team-filtered checkbox list. No backfill: the live population
  migrates by announcement.

Two consequences for this document: D4's allowlist decision is **settled, not proposed**, and its
open question 2 (the two-person setup flow) is now half-answered in practice — the Team Admin half
exists and works.

### Session token expiry (ADR-0054, PR #4204, merged 2026-08-14)

ADR-0040 expired a session's token on **dormancy**: a sliding window from `last_activity_at`, which
every user message advanced, so a caller who kept chatting held an admitted session forever.
[D7](#d7-admission-is-bounded-in-time) argued for replacing it, and **ADR-0054 did**: a token now stops
working `CHAT_SESSION_TOKEN_LIFETIME` (7 days) after the session was created, and activity does not
extend it. `last_activity_at` is no longer read for expiry.

Two things this document inherits: the lifetime is **mandatory** (with no dormancy rule behind it, a
session without one would have no expiry at all), and it is currently **one global value** — a channel
facing abuse cannot yet tighten below it, which is the override D7 still owes.

### From #3710

`OAuth2Application.team` (pinned, immutable), `OAuth2AccessToken.team`,
`is_client_credentials_request()`, `OAUTH_CLIENT_CREDENTIALS_SCOPES` (the explicit allowlist of scopes
a machine token may be granted, enforced at issuance by `APIScopedValidator.validate_scopes`), and
`ApiTestClient(auth_method="oauth_client_credentials")`.

### What remains open

| Path | Gate today |
|---|---|
| Embed key | `EmbeddedWidgetAuthentication` + `WidgetDomainPermission` + `WidgetAuthLevel` |
| Django session | membership **or** embed key (ADR-0053) |
| Session token on an existing session | ADR-0039 proof of possession |
| **Nothing at all** | **nothing** — `chat_start_session`'s only permission class is `WidgetDomainPermission`, which returns `True` when `request.auth` is not a channel |
| **A machine caller** | **no way in at all** — OAuth is not in the chat endpoints' `AUTH_CLASSES`. True of `/api/chat/*` only: a `chatbots:interact` token converses via `/api/openai/…/chat/completions` and `/channels/api/<experiment_id>/incoming_message` with **the chatbots its application lists** (PR #4198) — still with no *channel* enabling anything, which is the gap [D1](#d1-the-chat-api-channel-and-its-credential-mode) closes for this door |

## Decisions

### D1: The Chat API Channel and its credential mode

There is **one** channel that exposes a chatbot to the chat API, and a **mode** on it that says what a
caller must present. No new platform.

```python
# apps/channels/models.py
class ChannelPlatform(models.TextChoices):
    ...
    EMBEDDED_WIDGET = "embedded_widget", "Chat Widget & API"   # label change only


class CredentialMode(models.TextChoices):
    """What a caller must present to start a session on this channel. An admin's choice."""

    EMBED_KEY = "embed_key", "Embed key (public widget)"
    OAUTH = "oauth", "OAuth token"
```

| Want | Mode |
|---|---|
| A public embed, as today | `embed_key` (the default; every existing channel migrates to it) |
| An authenticated embed, or a machine integration | `oauth` |

**Two modes, not three.** A middle rung requiring the embed key *and* a token was considered and
dropped. It looks like defence in depth, but the two credentials are not independent: in a browser
embed the key ships in page source and the token is handed to page JavaScript, so whatever leaks one
leaks the other. It would only cover a token leaked from somewhere other than the page — host backend
logs, an intermediary — which is thin cover for a third mode.

The one real thing it bought was narrowing the token's reach. `chat:start` is *team*-scoped, so on its
own a token would admit its holder to any `oauth`-mode chatbot in the team, where the pair pinned it to
one chatbot. That pin is not lost — it moves to `OAuth2Application.allowed_chatbots`
([D4](#d4-what-makes-a-token-acceptable)), which scopes the credential directly instead of demanding a
second credential from the same browser. Dropping the middle mode is only safe *because* the allowlist
is in scope.

**In `oauth` mode the embed key is ignored, not rejected.** Existing snippets keep sending
`X-Embed-Key` and keep working; the mode check refuses them only if no valid token accompanies it.
Switching a live channel to `oauth` therefore needs no snippet change beyond adding `auth-token`.

**The stored platform value does not change**, only its label. `embedded_widget` is also a
`Participant.platform` value, so renaming it would fork every existing participant — the one-way split
ADR-0053 accepted once and should not repeat. Relabelling costs nothing and stops a server integration
wearing an "Embedded Widget" badge. See **Chat API Channel** in `CONTEXT.md`.

**Why a real column, not `extra_data`.** An earlier draft put `require_oauth` in `extra_data` because
it is already audited via `EXPERIMENT_CHANNEL_FIELDS` and needs no migration. ADR-0044 made the
opposite call for a field of exactly this nature: `required_auth_level` is durable per-channel
authorization policy and got a real column (`channels/migrations/0029_…`). `credential_mode` is the
same kind of thing, so it gets the same treatment — a `TextChoices` column, added to
`EXPERIMENT_CHANNEL_FIELDS`, defaulting to `EMBED_KEY`.

**The mode pins the auth level, and the DB enforces it.** `credential_mode` and `required_auth_level`
both talk about what the client presents, and they overlap on the embed key. The bad combination is
reachable and fails silently:

> An `oauth` channel sitting at `required_auth_level = EMBED_KEY` — where the ADR-0045 ratchet leaves a
> channel whose widget is old. `_issue_or_opt_out_session_token` sees `EMBED_KEY` and sets
> `session_token_required = False`, issuing **no token**. Every follow-up then lands in
> `_has_legacy_access`, where `request.auth` is not a channel, `embed_key_authorizes_channel` is False
> (the key is ignored in this mode, and a server caller has none), and `level != NONE`, so it returns
> `False`. `start/` returns `201` and `message/`, `poll/` and `upload/` all return `403`. The session is
> dead on arrival.

So `oauth` mode forces `required_auth_level = SESSION_TOKEN`, enforced in `clean()`/`save()` **and** by
a `CheckConstraint`, so the state is unrepresentable rather than merely un-enterable through the form.
`ratchet_widget_auth_levels` (ADR-0045) skips such channels: they are already at the top rung and could
only ever be a no-op.

**Not a new `WidgetAuthLevel` rung.** The two fields stay separate because the ladder is
version-ratcheted: an authorisation policy an admin chooses must never be switched on by a widget
upgrade.

**What this costs.** A chatbot has one Chat API Channel, so it cannot serve a public anonymous widget
*and* an authenticated integration simultaneously. Accepted: no such requirement exists today, and the
escape hatch is cheap — the one-channel-per-platform rule is a UI affordance (`for_dropdown` pops used
platforms), not a DB constraint, so allowing two Chat API Channels per chatbot is a later change to
the dropdown and a form guard, with no migration.

**Why a channel and not a boolean on `Experiment`.** "A channel is how a chatbot is exposed" is the
existing mental model; `ExperimentChannel` is audited, is not versioned (avoiding the version-copy
question a new `Experiment` field would raise), and gives the session a meaningful channel to belong
to.

**Participants are unaffected.** Every mode produces `platform=embedded_widget` participants, so there
is no namespace split to announce — a benefit of not adding a platform.

**The widget-capability abstraction is not needed.** An earlier draft introduced
`ChannelPlatform.widget_capable_platforms()` and `ExperimentChannel.is_widget_capable` to replace six
`platform_enum != EMBEDDED_WIDGET` guards, so a second platform could share the widget machinery. With
one platform, every one of those guards is already correct: version recording, deprecation badges,
sunset headers, `widget_auth_level`, `min_widget_version` and the inspect serializer all keep working
untouched. Version *recording* stays gated on `is_widget_request`, so a server caller in `oauth` mode
is not tagged with a placeholder version.

`MIN_OAUTH_WIDGET_VERSION` still earns its place in `apps/channels/widget_versions.py`: a browser
widget in `oauth` mode needs the release that ships the `auth-token` prop, and the channel dialog
should state it. It stays **advisory** — an older widget simply cannot send a token and fails
admission — so no new version-based rejection is introduced.

### D2: The origin rule follows the credential

ADR-0053 established that *each credential validates its own origin* — `embed_key_authorizes_channel`
folds the domain check into key validation rather than leaving it to `WidgetDomainPermission` alone.
The rule differs only on requests with no `Origin`/`Referer`:

| Mode | `allowed_domains` | Origin present | Origin absent |
|---|---|---|---|
| `embed_key` | required (≥1 domain or "allow all") | must match | **reject** — an embed key with no Origin is the abuse case (a stolen key used from `curl`) |
| `oauth`, list non-blank | — | must match | **reject** — the list declares this browser-facing |
| `oauth`, list blank | — | reject (an empty list matches nothing) | **admit** — server integration, authorised by the token |

**The domain list decides, not the mode.** An admin already declares which deployment a channel is by
filling the list or leaving it empty, so nothing extra is needed to distinguish them. A blank list
means **server-only**: any browser request is refused, which is the honest configuration for a machine
integration and avoids making an admin tick "allow all domains" to get it. A non-blank list means
browser-facing, and an originless request is refused exactly as it is under `embed_key`.

**The `oauth` + non-blank + originless rejection is what keeps the collapse to two modes safe.** The
dropped embed-key-*and*-token mode was inheriting "reject an originless request" from the embed key;
keying that on the domain list instead recovers the same protection, so a token leaked from a page
cannot be replayed from `curl` against a browser-facing channel.

The form makes `allowed_domains` required for `embed_key` and optional for `oauth`.

> A non-browser client that volunteers a `Referer` will be judged by it —
> `extract_domain_from_headers` reads `Origin` *or* `Referer`. Under `oauth` mode with a blank list
> that means rejection. Worth a line in the docs; not worth special-casing.

`embed_key_authorizes_channel` is unchanged: it already validates key and origin together and rejects
any platform that is not `EMBEDDED_WIDGET`, which stays correct. The OAuth rule lives with the OAuth
resolution ([D3](#d3-extend-the-existing-authorization-site)), so `WidgetDomainPermission` needs no
change at all.

> Earlier drafts also proposed enforcing the channel's domain list on *session-bound* requests. That
> is dropped: ADR-0039 makes the session token the credential for those endpoints, and a session token
> carries no origin semantics. Origin checks stay attached to the credentials that have one. Listed
> under [Out of scope](#deliberately-out-of-scope) as possible later hardening.

### D3: Extend the existing authorization site

ADR-0053 put the whole start-session rule in one function. The OAuth path becomes a branch of it
rather than a parallel gate:

```python
def _check_start_session_access(request, experiment, embed_key_channel, oauth_channel, version_number):
    """A 403/401 response if this caller may not start the session they asked for, else None.

    ADR-0053 established the rule for cookie-bearing callers: team membership or the chatbot's
    embed key. This adds the third credential — a client-credentials token, admitted only when
    the chatbot's Chat API Channel is in `oauth` mode.
    """
    if request.user.is_authenticated:
        ...  # unchanged ADR-0053 logic: membership, else embed key, else 403
    if version_number is not None:
        return Response({"error": "Version number requires authentication"}, status=403)
    if oauth_channel is not None:
        return None                 # the token was validated by the authenticator
    if embed_key_channel is not None:
        # The key resolved a channel, but the channel may not accept keys. Anonymous +
        # `oauth` mode is the leaked-embed-key case the mode exists to stop.
        if embed_key_channel.credential_mode != CredentialMode.EMBED_KEY:
            raise ChatApiAccessDenied()
        return None
    return None   # keyless: unchanged here; keyless-chat-start-sunset.md replaces this line
```

**The mode is checked against the channel the *embed key* resolved, and the check is a rejection
rather than a fallthrough.** An `oauth`-mode channel reached with a key and no token has to fail here
or nowhere: `embed_key_authorizes_channel` and `WidgetDomainPermission` are unchanged and know nothing
about the mode, and `ChatOAuthAuthentication` returns `None` when there is no `Authorization` header,
so it never runs on a key-only request. Leaving the branches to converge on `return None` — as an
earlier draft of this sketch did — would admit exactly the caller the test plan requires be refused
(*"Embed key alone, `oauth` mode → 401"*), which is to say it would ship a mode that silently does not
gate. The refusal is `ChatApiAccessDenied` (a `401`, per [D6](#d6-getting-a-401-out-of-drf)) rather
than a returned `403`, so it is indistinguishable from every other admission failure at this door.

Note that `oauth` mode ignores an embed key that rides along *with a valid token* — that is the
existing-snippet case from [D1](#d1-the-chat-api-channel-and-its-credential-mode). Ignored means "not
required and not rejected", not "sufficient".

The OAuth requirement is evaluated on the **embed-key branch only**, never on the membership branch —
ADR-0053 admits a cookie-bearing team member without a key, and switching a channel to `oauth` mode
must not lock team members out of their own in-app embeds.

**The OAuth credential is resolved by an authentication class, first in the list, on this endpoint
only.**

```python
# apps/api/views/chat.py
AUTH_CLASSES = [SessionAuthentication, EmbeddedWidgetAuthentication]            # unchanged
START_AUTH_CLASSES = [ChatOAuthAuthentication, *AUTH_CLASSES]
```

`ChatOAuthAuthentication` resolves the chatbot the way `EmbeddedWidgetAuthentication._get_experiment_id`
already does (`chatbot_id` from the body, through `get_working_version_id()` so channel lookups land on
the working version), finds its Chat API Channel if the mode is `oauth`, validates the token
([D4](#d4-what-makes-a-token-acceptable)) and the origin ([D2](#d2-the-origin-rule-follows-the-credential)),
and returns `(AnonymousUser(), channel)` — the same shape `EmbeddedWidgetAuthentication` returns.

Three things fall out of that shape, and they are why it is preferred to a resolver called from the
view:

- **The rate limiter needs no change at all.** `ChatAPIRateThrottle.identity` already buckets on
  `request.auth` when it is an `ExperimentChannel`, so the [ADR-0052 gap](#adr-0052-rate-limiting)
  closes for free. Bucketing is *per channel* rather than per OAuth application: two integrations
  talking to the same chatbot share an allowance, which matches how widget traffic already buckets and
  is in any case far better than sharing the client-IP bucket with unrelated anonymous traffic. Going
  per-application later is one branch in `identity`.
- **`401` stops needing a workaround.** `APIView.handle_exception` reads
  `authenticators[0].authenticate_header(request)` — the *first* authenticator in the list, not the one
  that matched — and downgrades `401` to `403` only when it returns `None`. With
  `ChatOAuthAuthentication` at position 0 returning `Bearer realm="api"`, authentication failures on
  this endpoint surface as `401`. See [D6](#d6-getting-a-401-out-of-drf).
- **Position 0 is required, not stylistic.** DRF stops at the first authenticator that matches. In
  `oauth` mode an existing snippet still carries `X-Embed-Key` — the key is ignored, not removed — so
  `EmbeddedWidgetAuthentication` would match first and no authenticator would ever validate the token.
  The view's mode check keeps that from being a silent bypass, but it needs a resolved token to admit
  anyone at all. With OAuth first the token is resolved, and ADR-0053's `get_embed_key_channel` remains
  available to the view for the `embed_key` mode.

**Why not the other four endpoints.** Keeping `START_AUTH_CLASSES` to `chat_start_session` is what
makes [D5](#d5-session-bound-endpoints-are-untouched) literally true, and it avoids a trap: on a
session-bound request, an OAuth-resolved channel in `request.auth` would be read by
`_has_legacy_access`'s `isinstance(request.auth, ExperimentChannel)` branch as *an embed key was
presented*. That is safe today only because [D1](#d1-the-chat-api-channel-and-its-credential-mode)
pins OAuth-mode channels to `SESSION_TOKEN`, which is too subtle to rely on.

**A presented-but-invalid token is never ignored.** An `Authorization` header that fails any check
raises `AuthenticationFailed` from the authenticator — a `401` — rather than returning `None` and
falling through to the next authenticator, or to the still-open keyless path.

`_resolve_experiment_channel` gains the OAuth channel as a further attribution source, keeping
ADR-0053's principle: the channel that owns the session is the one whose credential got the caller in.

### D4: What makes a token acceptable

| Check | Why |
|---|---|
| Validates via django-oauth-toolkit | Signature, expiry, revocation. |
| `authorization_grant_type == client_credentials` | This ticket is machine-to-machine (#3893, "Notes"). |
| `token.team_id == experiment.team_id` | A token pinned to team A must not reach team B's chatbot. |
| `token.is_valid([CHAT_API_SCOPE])` | `chat:start` — a new scope, narrower than `chatbots:interact`. See below. |
| The chatbot's Chat API Channel is in `oauth` mode | An admin must have exposed *this* chatbot. |
| The token's application lists this chatbot | An admin must have authorised *this* application for it. **Shipped** — ADR-0055. |

```python
# apps/oauth/permissions.py — sibling to is_client_credentials_request()
def validated_machine_token(request, experiment) -> OAuth2AccessToken:
    """The request's client-credentials token, or raise if it is not valid for `experiment`.

    The caller must already have established that an `Authorization` header is present:
    every path out of here is either a token or a refusal, never a silent None.
    """
    result = OAuth2Authentication().authenticate(request)
    if result is None:
        raise ChatApiAccessDenied()   # signature, expiry or revocation — never "no token"
    _user, token = result
    if not is_client_credentials_token(token) or token.team_id != experiment.team_id:
        raise ChatApiAccessDenied()
    if not token.is_valid([CHAT_API_SCOPE]):
        raise ChatApiAccessDenied()
    if not token_allows_chatbot(token, experiment):
        raise ChatApiAccessDenied()
    return token
```

**Absence is decided by the header, not by the authenticator's return value.**
`OAuth2Authentication.authenticate()` returns `None` for an *invalid or expired* token exactly as it
does for no token at all — the two differ only in that the first also sets `request.oauth2_error`.
Treating that `None` as "no credential was offered" would let an expired token fall through to
`EmbeddedWidgetAuthentication`, and from there to the still-open keyless path: a caller whose token
OCS had just revoked would be admitted anyway. So `ChatOAuthAuthentication` decides the question
before calling in — no `Authorization` header means return `None` and let the next authenticator run;
a header that is present and does not check out raises, whatever the reason. This is what makes
[D3](#d3-extend-the-existing-authorization-site)'s "a presented-but-invalid token is never ignored"
true in code rather than only in prose.

**Two small refactors, both the same shape.** `is_client_credentials_request(request)` becomes a
wrapper over a new `is_client_credentials_token(token)`, and PR #4198's
`application_allows_chatbot(request, experiment)` likewise becomes a wrapper over
`token_allows_chatbot(token, experiment)`. The second is not cosmetic: the shipped helper reads
`request.auth.application`, but `ChatOAuthAuthentication` resolves the token *inside* `authenticate()`,
before `request.auth` exists — so the request-shaped helper cannot be called from there. Extracting the
token-shaped core keeps the version normalisation (`get_working_version_id()`) in one place rather than
reimplementing it at the chat door, which is exactly the bug the shipped helper documents.

**Reuse, not re-decision.** The allowlist itself is settled, merged and recorded in **ADR-0055**
([above](#the-per-application-chatbot-allowlist-adr-0055-pr-4198-merged-2026-08-14)); what follows is why
`chat:start` needs it too rather than why it exists. Without it `chat:start` is *team*-scoped: one
token opens sessions on every `oauth`-mode chatbot in the team. That was tolerable while a middle mode
required the channel-scoped embed key alongside the token, because the key pinned the chatbot. Dropping
that mode ([D1](#d1-the-chat-api-channel-and-its-credential-mode)) removed the pin, and the token is
placed in a browser *by design* — so the team boundary is too coarse to be the last line.

**One difference at this door: the denial is a `401`, not `403`.** PR #4198's
`enforce_application_chatbot_access` raises `PermissionDenied` because on those endpoints the caller
authenticated fine and only failed authorisation. On `chat/start/` the allowlist is one of several
admission checks that [D6](#d6-getting-a-401-out-of-drf) collapses into a single uniform
`401 chat_access_denied`, so this branch calls the **predicate** and lets the authenticator raise —
it must not call `enforce_…`, which would leak which check failed.

**Empty means none, not all.** An application authorises nothing until someone says so, which is the
same deny-by-default rule the channel already applies — the two are complementary: the channel says
*this chatbot is exposed over OAuth*, the application says *this credential may use it*. Both must
agree.

**Why on the application rather than the channel.** `docs/architecture/package-map.md` puts `oauth` in
the entry-point tier and `channels` in domain & runtime, with dependencies flowing downward. A
`channels → oauth` reference inverts that; `oauth → experiments` does not. Placement aside, it also
reads correctly: this scopes a *credential*, the way `redirect_uris` and the grant type already do, and
it gives one place to audit an application's total reach rather than a list per channel. The cost is
that exposure is now described in two places, and that the Team Admin who registers the application
(holding `oauth.*`) is often not the Chatbot Admin who creates the channel (holding `bot_channels.*`) —
the same permission split the notification work in
[keyless-chat-start-sunset.md](keyless-chat-start-sunset.md) also has to work around.
The team-filtered picker shipped with the field; `OAuth2Application.as_chip()` and the
`oauth_applications` reverse accessor cover rendering the reverse view on a chatbot.

**Authorization-code applications are unaffected** — `application_allows_chatbot` returns `True` for
every non-client-credentials caller, so the field neither appears on nor constrains a user-facing
application.

**It already gates the existing `chatbots:interact` endpoints**, not just this one: `/api/openai/`,
the `channels/api/…/incoming_message` ingress views and `TriggerBotMessage`, six views in all. Leaving those on plain team scope would
have kept the inconsistency this field exists to remove — the same scope meaning different things at
different doors. Empty means none there too, with no backfill: client credentials shipped 2026-07-24,
so the population of live machine applications was small enough to migrate by announcement. That work
was a **prerequisite** for this document and **shipped ahead of it in PR #4198**, so `chat:start`
inherits a door that is already consistent with the others.

**Authorization-code tokens are refused.** Their team comes from a `Grant` plus a live membership
check, and admitting them raises a question this ticket does not need to answer (may a signed-in
user's token chat as an anonymous participant?). The grant-type branch exists from day one, so
admitting them later is a small change plus tests.

**A new scope `chat:start` is required, and `chatbots:interact` is refused.** An earlier draft reused
`chatbots:interact` on the grounds that it already means "converse with a chatbot". It means
considerably more than that. Today a client-credentials token bearing it can, for **every** chatbot
in the team and with no channel enabling anything:

| Endpoint | Capability |
|---|---|
| `apps/api/openai.py` (`ChatCompletions*View`) | converse with any chatbot |
| `apps/channels/views.py` (`NewApiMessage*View`) | converse with any chatbot |
| `apps/api/views/channels.py`, `apps/api/v2/channels.py` (`TriggerBotMessageView`) | **send outbound WhatsApp / Telegram / Connect messages to arbitrary participants** |

That is the wrong credential to hand a browser. The supported shape for a browser embed puts a bearer
token in page JavaScript; if that token is `chatbots:interact`, "hardening an existing embed" would replace a
channel-scoped embed key with a team-wide outbound-messaging capability — a large escalation in the
name of a security improvement.

So `chat:start` is added to `OAUTH2_PROVIDER["SCOPES"]` ("Start a chat session") and to
`OAUTH_CLIENT_CREDENTIALS_SCOPES`, and `/api/chat/start/` accepts **only** that scope. A
`chatbots:interact` token is refused there. Requiring it exclusively is what makes the narrowing
real: a host cannot reach for the broad token it already has, so the page token is narrow by
construction. Nothing breaks — no OAuth caller can reach this endpoint today — and the cost is that a
server-only integration requests one extra scope.

The scope authorises exactly one endpoint, which is honest rather than brittle: [D5](#d5-session-bound-endpoints-are-untouched)
keeps `message/`, `poll/` and `upload/` on ADR-0039's session token, so there is no second endpoint
for it to grow onto. It also does not collide with `sessions:write` — `ExperimentSessionViewSet` is
list/retrieve only, so no scope creates a session today.

> **Residual foot-gun.** There is no per-application scope restriction: any client-credentials
> application may request any scope in `OAUTH_CLIENT_CREDENTIALS_SCOPES`. So this gives a host the
> *ability* to mint a narrow page token; it does not prevent a careless host from minting a broad one
> and putting it in the page. `allowed_chatbots` bounds *which chatbots* such a token reaches, but not
> which other APIs — a broad-scope token in a page still reaches `/api/openai/` and
> `TriggerBotMessageView`. Closing that needs a per-application *scope* allowlist, noted under
> [Out of scope](#deliberately-out-of-scope). Docs must say plainly: request `chat:start` alone for any
> token that reaches a browser.

### D5: Session-bound endpoints are untouched

`message/`, `poll/`, task-`poll/` and `upload/` keep ADR-0039's rules exactly: a valid
`X-Session-Token` for that session, or an authenticated user who *is* the session's participant.
`SessionAccessPermission` needs no change for this work, and neither does their `AUTH_CLASSES` —
[D3](#d3-extend-the-existing-authorization-site) scopes `ChatOAuthAuthentication` to
`chat_start_session` alone, which is what makes this decision literal rather than aspirational.

Admission therefore belongs at creation, and the session token carries it forward **for a bounded
time** ([D7](#d7-admission-is-bounded-in-time)) — which is the model ADR-0039 chose deliberately, not
a shortcut taken here. Three consequences worth stating:

- The participant web-chat page (`web_chat.html`) and the continue-session launcher keep working
  untouched; ADR-0053 already notes they never reach `chat_start_session`.
- An OAuth-created session does **not** require the bearer token on subsequent calls. The host app may
  hand the browser only the session token, which is the smaller capability.
- A 10-hour OAuth token therefore cannot expire mid-conversation and break an open chat. What *can*
  end a conversation is the session's own absolute lifetime (D7), which is a deliberate re-admission
  point rather than a token-expiry accident.

### D6: Getting a `401` out of DRF

DRF coerces `NotAuthenticated`/`AuthenticationFailed` to `403` unless
`authenticators[0].authenticate_header()` returns a value (`APIView.handle_exception`). Note
`authenticators[0]` — the *first authenticator in the list*, not the one that matched. With
`SessionAuthentication` first that is `None`, which is why an invalid embed key returns `403` today
and why ADR-0053's denials are `403`s.

[D3](#d3-extend-the-existing-authorization-site) puts `ChatOAuthAuthentication` at position 0 of
`START_AUTH_CLASSES`, and its `authenticate_header` returns `Bearer realm="api"`. So on
`chat_start_session` the coercion no longer fires and `401`s come out as `401`s. An earlier draft
needed a dedicated exception carrying an explicit `auth_header` to work around this; that workaround
is gone.

The exception survives in shrunken form, for the **body**, which DRF does not provide:
`rest_framework.views.exception_handler` passes a `dict` detail straight through, and the widget's
error surface keys on the `code`.

```python
# apps/api/exceptions.py
class ChatApiAccessDenied(NotAuthenticated):
    default_detail = {"error": "Authentication required to chat with this chatbot", "code": "chat_access_denied"}
```

**What this changes, and what it does not.** Exactly one existing assertion flips:
`test_embedded_widget_auth.py::test_start_session_with_invalid_embed_key`, `403` → `401`. The other
three `403`s in that module are on `send-message` and `poll`, which keep today's `AUTH_CLASSES`. And
`SessionAccessPermission` raises `PermissionDenied`, which DRF never coerces, so the widget's
session-token recovery path (`chat-session-service.ts`, `ocs-chat.tsx`) is untouched. ADR-0053's
denials are plain `Response(..., status=403)` objects rather than exceptions, so they are unaffected
too — authenticated callers keep the `403` and its `"You do not have access to this chatbot"` body. A
`401` at an authenticated user reads as a broken session and invites a pointless re-login.

**One `code` for every denial reason.** The body does not distinguish "chatbot not exposed", "bad
token", "wrong team", "expired token" and "disallowed origin"; details go to the logs. A caller that
has legitimately misconfigured one of these gets no more help from the response than an attacker
probing for which one it is.

> **Chatbot existence is not a secret, and this does not try to hide it.** `get_object_or_404` runs
> before the access check, so an unknown `chatbot_id` is a `404` while a known-but-unexposed chatbot
> is a `401` — the status distinguishes them however uniform the body is. That is deliberate, not an
> oversight: ADR-0053 states plainly that `public_id`s are not secret (they ship in every embed
> snippet), they are UUIDs, and an attacker must already hold one for the distinction to tell them
> anything. Collapsing the `404` into the `401` would cost an integrator with a typo'd `chatbot_id`
> the only signal that says so.

### D7: Admission is bounded in time

> **Largely shipped.** The global lifetime landed in PR #4204 and is recorded as **ADR-0054**
> (*Chat session tokens expire on absolute age, not inactivity*), superseding ADR-0040's expiry rule.
> [#4199](https://github.com/dimagi/open-chat-studio/issues/4199) stays open for the per-channel
> `session_token_lifetime` override, which ADR-0054 records as deferred. The argument below is kept
> because it is what makes the rest of this document's admission control worth anything; read it as
> settled, not proposed.

Everything above governs **admission**, and [D5](#d5-session-bound-endpoints-are-untouched) says
admission happens once per session. Short OAuth token lifetimes therefore bound how often a host can
*mint* a credential, not how long the resulting access lasts. ADR-0040's window slid on
`last_activity_at`, so a caller who kept chatting kept its session alive forever. The abuse budget of
one minted token is

```text
rate limit (ADR-0052, per-session bucket)  ×  session lifetime
```

and the second factor is currently unbounded, which makes the first one moot. That matters most for
exactly the case #3893 exists to serve: a host that switches to `oauth` mode to stop its widget being
used as a free chatbot gets a stricter door and the same unlimited room behind it.

So the sliding window is **replaced** by an absolute lifetime measured from `created_at`:

```python
# apps/api/session_tokens.py
def session_token_expired(session: ExperimentSession) -> bool:
    """A session's token stops working a fixed time after the session was created."""
    channel = session.experiment_channel
    lifetime = (channel and channel.session_token_lifetime) or settings.CHAT_SESSION_TOKEN_LIFETIME
    return timezone.now() - session.created_at > lifetime
```

**Replaced, not joined — the two cannot coexist usefully.** An earlier draft added the cap *beside*
the window and kept both. That does not work: `last_activity_at >= created_at`, so
`now - last_activity <= now - created_at`, and therefore any lifetime `L <= W` makes the inactivity
branch **unreachable** — whenever it would fire, the cap has already fired. At `L = 7 days` against
today's `W = 7 days` the window is provably dead code, and dead code that reads like live policy is
worse than no policy. The two only compose when `L > W` ("a dormant session dies in a week, an active
one dies at 90 days"), which no configuration here wants.

**Which forces the lifetime to be mandatory.** With the window gone, a session with no lifetime has *no
expiry at all* — precisely the "no expiry" alternative ADR-0040 rejected and the indefinite access
ADR-0039 exists to prevent. So there is no "off": `CHAT_SESSION_TOKEN_LIFETIME` always has a value and
the per-channel field only ever *overrides* it. This is why the lifetime cannot be opt-in, and it settles
what an earlier draft left as an open question.

**One number, one sentence.** The resulting rule is *a chat session is valid for N from creation* —
no interaction between two mechanisms to reason about, and one value per channel to explain to an
admin. `last_activity_at` is no longer read here at all; ADR-0040's care that polling must not advance
it stops mattering for expiry (the field still drives session ordering in the UI).

**The rest of ADR-0040's model is untouched.** The token stays stateless, unstored and re-derivable;
`created_at` is already on the row and already read by the branch being deleted. There is one call site
(`SessionAccessPermission._token_grants_access`) and it already raises `403 {"code": "session_expired"}`,
so no new response shape and no new error path.

**The widget needs no change — but only an *unbound* one.** An earlier draft of this section claimed
the shipped `session_expired` recovery covers every widget. Implementing it (PR #4204) showed that is
half true, and the missing half is the sharpest cost of this decision:

| Widget | On expiry |
|---|---|
| **Unbound** (`chatbot-id`, starts its own session) | existing `SessionAccessError` recovery starts a new conversation |
| **Bound** (handed `session-id` + `session-token` by the host page — the full-page and kiosk chat) | refuses to restart a session it does not own: *"This chat session is no longer available"*, with no affordance on that page |

Under the inactivity window a bound widget only dead-ended on an already-abandoned session. Now an
anonymous participant whose conversation is still in daily use hits the wall on day 7 and must re-enter
through the chatbot's start URL. ADR-0054 names giving those pages a "start a new chat" affordance as
the follow-up it owes them. This does not touch the `oauth` path — an `oauth`-mode embed is unbound by
construction — but it is the reason the lifetime is not a free change.

**The restart is the point.** When the lifetime fires in `oauth` mode, the widget's recovery calls
`chat/start/` again — which now needs a valid bearer token ([D4](#d4-what-makes-a-token-acceptable)).
If the page's token has expired, that is a `401` and the host must push a fresh one through the
`auth-token` `@Watch`. Admission becomes **recurring rather than one-shot**, so whatever check the host
puts in front of its own token-minting endpoint runs again at the cadence of the lifetime. That is the
mechanism by which a short OAuth TTL finally does the work it looks like it is doing.

**Configuration: a mandatory global (shipped), with a per-channel override (outstanding).**
`CHAT_SESSION_TOKEN_LIFETIME` always has a value and is live. `ExperimentChannel.session_token_lifetime`
(nullable — null means "use the global") is **the piece #4199 still tracks**, and it rides D1's
migration rather than carrying one of its own. `get_experiment_session_cached` already
`select_related`s `experiment_channel` and caches the session, so the override costs no query.

Per-channel matters because the modes want different values: on a public `embed_key` widget a
mid-conversation restart is pure UX cost with no security gain, while an `oauth` channel wants it
tight. **Until the override lands, an `oauth` channel cannot tighten below the global at all** — so a
chatbot exposed for abuse-resistance still grants a week per admitted caller. That makes the override
a soft prerequisite for this document's own goal, not merely a nice-to-have.

**The global default is 7 days — deliberately today's number.** Reusing `W` makes the change a
*uniform tightening*: since the old rule expired a session at `last_activity + 7d` and
`last_activity >= created_at`, the new rule at `created_at + 7d` fires no later, ever. **No session's
life is extended and none dies before it would have under a dormancy rule.** The only sessions affected
are those currently kept alive past a week *by activity* — which is exactly the abuse shape, plus a
thin tail of genuine long-running conversations.

The real mitigation is the per-channel override, not the global: an abuse-facing `oauth` channel sets
**4–12 hours** — comfortably longer than one real conversation, and no shorter than the OAuth token's
own TTL, since the host must re-mint either way. A 7-day global is a safe floor, not a defence; a
week of free chatbot per admitted caller is still a lot, and a channel that cares must say so.

> **It was still a behaviour change on an already-admitted path**, and the only one in this document.
> A participant who returned to a widget daily rode the sliding window indefinitely; the conversation
> now ends a week after it started. For an unbound widget the cost is conversation *continuity* — the
> history stays on the old session and the participant stops seeing it. For a bound one it is the
> dead-end above, which is the more serious half and remains open.

**What this does not fix.** A host that will hand a token to anyone who asks is still handing out
sessions; the lifetime makes the gate recurring, not stronger. Bounding *that* is the host's business logic,
which is the correct place for it — OCS cannot know who the host's users are. What OCS now guarantees
is that the question gets asked again.

**Not a per-session message budget.** Capping total messages was considered and dropped as redundant:
rate limit × bounded lifetime is already a finite number, and a budget needs a counter where the lifetime
needs nothing. Noted under [Out of scope](#deliberately-out-of-scope) if a harder ceiling is ever
wanted.

**Not per-request bearer tokens in `oauth` mode.** The obvious alternative — drop D5's exemption and
require the token on `message/`, `poll/` and `upload/` — is worse: it forfeits "a 10-hour token cannot
expire mid-conversation", obliges the host to keep a live token in page JavaScript continuously
instead of handing one over and forgetting it, and re-opens the `_has_legacy_access` trap
[D3](#d3-extend-the-existing-authorization-site) avoids by scoping `ChatOAuthAuthentication` to
`start/`. The cap gets most of the benefit at a fraction of the cost.

## Browser embeds and the token

Issue #3893's ask is `oauth` mode on a browser embed: the same snippet, now requiring a valid
client-credentials token ([D4](#d4-what-makes-a-token-acceptable)), checked at `start/` like every
other admission decision. Any `X-Embed-Key` the existing snippet still sends is ignored.

One interaction to respect: ADR-0053 lets a cookie-bearing **team member** in without an embed key, so
the OAuth requirement is evaluated on the embed-key branch only — otherwise switching a channel to
`oauth` would lock team members out of their own in-app embeds.

Client credentials means a client secret, which must never reach page source. The supported shape is:
the host app's backend mints a short-lived token and hands it to the page.

```text
host backend ──POST /o/token/ (client_credentials)──► OCS
      │  short-lived access token
      ▼
   widget ──Authorization: Bearer … [+ X-Embed-Key]──► POST /api/chat/start/
      │  session token in the response
      ▼
   widget ──X-Session-Token──► message / poll / upload
```

The page token carries `chat:start` and nothing else ([D4](#d4-what-makes-a-token-acceptable)), so a
leak costs the ability to start chat sessions — not the team-wide outbound-messaging capability
`chatbots:interact` would have handed over.

Widget changes (`components/chat_widget`): an `auth-token` prop threaded into
`ChatSessionServiceOptions` and emitted from `getCommonHeaders()`, a `@Watch` so the host can rotate
it, an error surface for a `401` with `code: "chat_access_denied"`, and **no** `localStorage`
persistence — the token belongs to the host page, not the session. `embed-key` stays optional, so an
`oauth`-mode embed may set only `chatbot-id` + `auth-token`.

Server-side, `authorization` must be added to `CORS_ALLOW_HEADERS`, or the cross-origin preflight
rejects the request before the view runs. Safe here: `CORS_URLS_REGEX` limits CORS to `^/api/chat/.*$`
and `CORS_ALLOW_CREDENTIALS = False`, so allowing the header only permits what the page's own JS sets
— no ambient credentials.

Per the widget-consumption model the Django app installs the **published npm** widget, so the
end-to-end flow needs a widget release plus a `LATEST_VERSION` bump; that release number becomes
`MIN_OAUTH_WIDGET_VERSION` ([D1](#d1-the-chat-api-channel-and-its-credential-mode)). The server work is
independent and backwards compatible, so it lands first.

## Implementation outline

Backwards compatible throughout: every path admitted today is still admitted. ADR-0053 already
delivered what earlier drafts listed as the membership check and the `ChatWidgetConfig` work, and
PR #4198 delivered the per-application allowlist, so none of those appear here as work.

| # | File | Change |
|---|---|---|
| 1 | `apps/channels/models.py` | `CredentialMode` choices; `credential_mode` column defaulting to `EMBED_KEY`; nullable `session_token_lifetime` (D7; null = use the global); `EMBEDDED_WIDGET` label → "Chat Widget & API"; `clean()`/`save()` + `CheckConstraint` pinning `SESSION_TOKEN` for `oauth` mode; add both columns to `EXPERIMENT_CHANNEL_FIELDS`. **Migration.** |
| 2 | `apps/channels/widget_versions.py` | `MIN_OAUTH_WIDGET_VERSION` (advisory). |
| 3 | `apps/channels/forms.py` | `credential_mode` on `EmbeddedWidgetChannelForm`; `allowed_domains` required for `embed_key`, optional for `oauth`; `session_token_lifetime` as an optional override with the mode's suggested value in help text; instructions + link to the team's OAuth applications. |
| 4 | `apps/channels/tasks.py` | Ratchet task skips channels already pinned to `SESSION_TOKEN` by their mode. |
| 5 | `apps/oauth/models.py`, `forms.py`, `views.py` | ~~`allowed_chatbots` M2M + team-filtered picker~~ — **shipped in PR #4198**. No change. |
| 6 | `apps/oauth/permissions.py` | `is_client_credentials_token()` and `token_allows_chatbot()` extracted from their request-shaped wrappers (D4 — the wrappers read `request.auth`, which does not exist inside an authenticator); `validated_machine_token()` composing them. |
| 7 | `apps/api/exceptions.py` | `ChatApiAccessDenied(NotAuthenticated)` — body and `code` only; the `401` comes from the authenticator (D6). |
| 8 | `apps/api/authentication.py` | `ChatOAuthAuthentication`: resolve chatbot → Chat API Channel in `oauth` mode, validate token + origin, return `(AnonymousUser(), channel)`; `authenticate_header` → `Bearer realm="api"` (D3). |
| 9 | `apps/api/throttling.py` | **No change** — the existing `ExperimentChannel` branch buckets OAuth callers per channel. |
| 10 | `apps/api/session_tokens.py` | ~~age check against `created_at`; `last_activity_at` branch deleted~~ — **shipped in PR #4204** (ADR-0054). Reading the per-channel override is what remains. |
| 11 | `apps/api/permissions.py` | **No change** — one call site, already raising `session_expired`. |
| 12 | `apps/api/views/chat.py` | `START_AUTH_CLASSES` on `chat_start_session` only; mode checks in `_check_start_session_access`; OAuth channel as an attribution source in `_resolve_experiment_channel`. |
| 13 | `apps/api/v2/inspect/serializers.py` | **No change** — one platform, so the existing `EMBEDDED_WIDGET` guards stay correct. Expose `credential_mode` if the inspect API should report it. |
| 14 | `config/settings.py` | `CORS_ALLOW_HEADERS += ["authorization"]`; `chat:start` in `OAUTH2_PROVIDER["SCOPES"]` and `OAUTH_CLIENT_CREDENTIALS_SCOPES`; `CHAT_API_SCOPE`. `CHAT_SESSION_TOKEN_LIFETIME` already **shipped in PR #4204**. |
| 15 | `components/chat_widget` | `auth-token` prop, header, `@Watch`, `401` surface; release + `LATEST_VERSION` bump. Nothing for D7 in an *unbound* widget; the bound-widget dead-end (D7) is ADR-0054's own follow-up, not this document's. |

This work carries **one migration** (`credential_mode` + `session_token_lifetime` on the same
`channels` migration; the platform change is a label only, and `allowed_chatbots` already shipped as
`oauth.0004`), and lands as a single phase. No
change to `required_auth_level` semantics for existing channels — they all migrate to `EMBED_KEY` mode,
which is exactly today's behaviour. One status code changes on an already-rejected path
(`test_start_session_with_invalid_embed_key`, `403` → `401`, [D6](#d6-getting-a-401-out-of-drf)).

**[D7](#d7-admission-is-bounded-in-time) was the one behaviour change on an already-admitted path**,
and it has already shipped separately (PR #4204, ADR-0054), so **this document is now backwards
compatible end to end**. What remains of D7 here is the per-channel `session_token_lifetime` override
(rows 1 and 3), still tracked by [#4199](https://github.com/dimagi/open-chat-studio/issues/4199). It
rides D1's migration; if it lands first it carries its own and D1's shrinks back to `credential_mode`
alone.

**ADRs to extract** when this flips to `stable` (next free number is 0056): the admission model, with
the Chat API Channel and its credential mode as the enablement rule, and the OAuth credential's
acceptance conditions (D1–D4). The allowlist half of D4 is **already recorded as ADR-0055** and should
be cited rather than re-extracted; what remains for D4 is the `chat:start` scope and the conditions
specific to the chat door. D7 needs no extraction — it is **ADR-0054**, already written, superseding
ADR-0040's expiry rule. The keyless cutover has its own document and its own ADR.

## Test plan

New `apps/api/tests/test_chat_api_admission.py`, parametrised over the credential paths. The
membership and embed-key rows are ADR-0053's and already covered in `test_chat_api_authed.py`; the
allowlist's own semantics are PR #4198's and covered in `test_application_chatbot_allowlist.py`, so
what follows tests the *chat door*, not the allowlist:

| Case | Expected |
|---|---|
| Machine token, `oauth` mode | `201`, session on that channel |
| Machine token, channel in `embed_key` mode | `401` — the mode is the enablement |
| Machine token for a different team | `401` |
| Machine token whose application does **not** list this chatbot | `401` — the allowlist is the per-chatbot pin, and a `401` here rather than PR #4198's `403` (D4) |
| Machine token addressing a **version** of a listed chatbot by its own `public_id` | `201` — `token_allows_chatbot` keeps PR #4198's `get_working_version_id()` normalisation |
| Machine token valid for chatbot A, replayed against chatbot B in the same team | `401` — the leak-a-page-token case |
| Application with an empty `allowed_chatbots` | `401` — empty means none, not all |
| Machine token carrying `chatbots:interact` but not `chat:start` | `401` — the narrow scope is mandatory (D4) |
| Machine token expired / revoked | `401` |
| Authorization-code token | `401` |
| Valid token + a stale/invalid `X-Embed-Key`, `oauth` mode | `201` — the key is ignored, not rejected |
| Embed key alone, `oauth` mode | `401` — the key does not admit |
| `oauth` mode, cookie-bearing **team member**, no key or token | `201` — ADR-0053 path unaffected |
| Machine token + `version_number` | `403` — version selection stays member-only |
| Invalid token offered on an `embed_key`-mode chatbot | `401` — never silently falls through to the keyless path |

**Ordering guard** (the load-bearing part of D3): an `oauth`-mode request carrying *both*
`X-Embed-Key` and `Authorization` must be authenticated by `ChatOAuthAuthentication`, not
`EmbeddedWidgetAuthentication` — the realistic shape, since existing snippets keep sending the key. A test asserting `START_AUTH_CLASSES[0] is ChatOAuthAuthentication`
is worth having alongside it — the `401` behaviour in [D6](#d6-getting-a-401-out-of-drf) depends on
that position, and a reorder would silently reintroduce `403`s.

Domain rule (`test_chat_api_domains.py`), parametrised over mode × origin × list:

| Mode | List | Origin | Expected |
|---|---|---|---|
| `embed_key` | `[a.com]` | absent / `a.com` / `b.com` | reject / admit / reject (unchanged) |
| `oauth` | `[]` | absent / any | admit — server-only / reject |
| `oauth` | `[a.com]` | absent / `a.com` / `b.com` | **reject** / admit / reject |
| `oauth` | `[ALL_DOMAINS]` | absent / any | **reject** / admit |

Throttle identity (`test_throttling.py` extension): an OAuth caller buckets on its channel, not the
client IP — two chatbots' integrations do not share an allowance, and an OAuth caller does not consume
the IP bucket of anonymous traffic from the same egress IP.

Session lifetime ([D7](#d7-admission-is-bounded-in-time)). The global rule is covered by PR #4204 in
`test_session_tokens.py` and `test_chat_session_token.py`; only the rows below are still owed:

| Case | Expected |
|---|---|
| Channel sets `session_token_lifetime` | the channel value wins over the global setting |
| Channel null, or session with no channel | the global setting applies |
| Lifetime fires, then `chat/start/` in `oauth` mode with an **expired** bearer token | `401` — the re-admission point actually re-checks the credential |
| Lifetime fires, then `chat/start/` in `oauth` mode with a **fresh** bearer token | `201`, a new session |

The third row carries D7's argument and belongs to this document rather than to ADR-0054: the bound is
worth nothing if the restart is admitted without re-crossing the OAuth gate.

Mode/level invariant (`test_widget_auth_level.py` extension): `oauth` mode forces
`required_auth_level = SESSION_TOKEN`; the `CheckConstraint` rejects the combination at the DB, so the
dead-session path in [D1](#d1-the-chat-api-channel-and-its-credential-mode) is unrepresentable. Assert
end-to-end too — start a session in `oauth` mode and confirm a token is issued and `poll/`
succeeds with it, because that is the failure the constraint exists to prevent.

Widget regression guard: the existing `session_expired` recovery in
`ocs-chat_session_handling.spec.tsx` stays green — nothing here adds an error surface, and the
bound-widget gap ([D7](#d7-admission-is-bounded-in-time)) is ADR-0054's follow-up, not this one's.

Regression guards: `test_chat_api_authed.py` status codes stay `403`;
`test_embedded_widget_auth.py::test_start_session_with_invalid_embed_key` moves to `401` and the other
three stay `403` (they are on session-bound endpoints); `test_chat_session_token.py`,
`test_widget_version_tracking.py` and `test_chat_api_anon.py` stay green — the anonymous keyless path
is untouched by this work. Widget: header sent/omitted/rotated, never persisted.

## Deliberately out of scope

- **Trusted participant identity.** An OAuth caller could legitimately *assert*
  `participant_remote_id`, giving cross-session continuity instead of a fresh `anon:<uuid>` — a
  participant-modelling change, not access control → **follow-up issue**.
- **Authorization-code (user) tokens** on the chat API (D4).
- **Per-application *scope* restriction** on `OAuth2Application` — would let a Team Admin register a
  widget application that can only ever mint `chat:start` tokens, closing the residual foot-gun in
  [D4](#d4-what-makes-a-token-acceptable). Needs a model field, form work and a migration.
- **Per-application rate *limits*.** D3 fixes how OAuth traffic is *bucketed*; choosing different
  allowances per application is ADR-0052's territory, not this document's.
- **Per-session message budget.** A hard ceiling on messages per session, rather than
  [D7](#d7-admission-is-bounded-in-time)'s ceiling on *time*. Redundant once the lifetime exists —
  rate limit × bounded lifetime is already finite — and it needs a counter where the lifetime needs
  nothing.
- **Dormancy expiry alongside the lifetime.** Only expressible as `lifetime > 7 days` on some channel
  (D7), which nothing wants today. If long-lived conversations ever become a product need, the deleted
  `last_activity_at` branch is the thing to restore.
- **Requiring the bearer token on session-bound requests** in `oauth` mode (see D7) — a larger change
  to D5's model for a small gain over the lifetime.
- **Origin checks on session-bound requests** (see the note in D2) — possible later hardening, and a
  behaviour change for existing widget sessions.
- The non-API chat surfaces (Django web-chat views, messaging platforms) — untouched.

## Open questions

1. **Which release becomes `MIN_OAUTH_WIDGET_VERSION`?** Decided when the widget release lands; the
   channel dialog and docs quote it.
2. **Does the two-person setup flow hold up?** `oauth` mode needs a Team Admin (registers the
   application, names its chatbots) *and* a Chatbot Admin (creates the channel, sets the mode). Neither
   can finish alone. The Team Admin half is now live (PR #4198) and can be observed in use before the
   channel half is built — worth watching, since the fallback (moving the allowlist onto the channel
   and accepting the layering cost, [D4](#d4-what-makes-a-token-acceptable)) gets more expensive to
   take once the field is in production.
3. ~~**Does the session lifetime ship with a global default at all?**~~ **Settled and shipped** as
   ADR-0054: it must, because deleting the inactivity window leaves an unbounded session wherever no
   lifetime is set. `CHAT_SESSION_TOKEN_LIFETIME` is always set, at 7 days.
4. **Does the bound-widget dead-end block `oauth` mode?** ADR-0054 leaves a bound widget with no
   restart affordance when a session ages out. An `oauth`-mode embed is unbound, so this document is
   not blocked by it — but both surfaces share the `session_expired` path, and a fix landing there
   should be checked against the [D7](#d7-admission-is-bounded-in-time) restart flow rather than
   designed for the bound case alone.
