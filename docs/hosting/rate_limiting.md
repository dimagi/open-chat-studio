# Rate Limiting

Open Chat Studio counts inbound requests per surface and can refuse the ones over budget. It
ships counting only. A new deployment records what it would have blocked without turning anyone
away, and enforcement is switched on separately once you have numbers to set it from.

This page covers the switch-on. The variables themselves are listed in the
[Configuration Reference](configuration.md), and the reasoning behind an in-house limiter rather
than a library is in
[ADR-0052](https://github.com/dimagi/open-chat-studio/blob/main/docs/adr/0052-app-layer-rate-limiting-mechanism.md).

## How Counting Works

Every request resolves to a **scope**, meaning the surface it arrived on, and an **identity**,
meaning whose budget it spends. That pair addresses a counter in Redis which resets on a fixed
window.

| Scope | Default | Covers |
| --- | --- | --- |
| `api` | 2000/5m | the authenticated REST API |
| `admin_api` | 100/5m | `/admin/api/*` autocomplete and provider reporting |
| `chat_api` | 300/5m | `/api/chat/*`, used by the embedded widget and other clients |
| `public_chat` | 100/5m | the public web chat views |
| `channels` | 3000/5m | inbound deliveries from messaging providers |
| `credentials` | 100/5m | OAuth token endpoints, rejected API keys, Connect key exchange |

What counts as an identity differs by scope: a team, an API key, a chat session, a widget
channel, an identifier carried in a webhook URL, or the caller's address where nothing better is
available. The Configuration Reference gives the order each scope tries.

Counters live in their own Redis cache alias with a half-second socket timeout, so an unhealthy
Redis cannot stall request handling.

## Before You Turn It On

**Set `RATE_LIMIT_TRUSTED_PROXY_COUNT` to the number of proxies in front of the app.** It
defaults to `0`, meaning the address is read from the connection itself. Behind a load balancer
or a tunnel that address belongs to the proxy, not the caller, so every caller collapses into one
bucket and any address-keyed scope stops distinguishing them. Of everything on this page, this is
the most likely cause of enforcement dropping traffic that should have been served.

Check it before enforcing rather than after. While the limiter is only counting, a wrong value
produces one implausibly busy bucket and no other symptom, which is easy to read as ordinary
traffic.

**Then leave it counting long enough to see a normal peak.** The shipped rates are starting
points picked to be generous. They are not measurements of any real deployment, and a window that
misses your busiest hour will hand you a threshold that refuses real traffic the first time that
hour comes round.

## Reading The Counters

Everything goes to the `ocs.rate_limit` logger.

### rate_limit.would_block

An identity went over its limit and was served anyway. While `RATE_LIMIT_ENFORCE` is `False` this
is what every crossing produces.

| Field | Meaning |
| --- | --- |
| `scope` | which surface |
| `identity_type` | what kind of thing the budget belongs to |
| `key_hash` | first 12 characters of the SHA-256 of the identity |
| `count` | requests this identity has made in the current window |
| `limit` | the configured limit |
| `team_id` | the team, where the request resolved one |

`key_hash` is a one-way digest rather than the identity itself, so these lines can be retained
without carrying customer identifiers. It stays stable within a window, which is enough to tell
one busy caller from many. To put a name to one, use `team_id`; where that is absent, you would
have to hash candidate identities offline and compare.

Not every crossing is logged. The request that crosses the limit always is, and after that only
requests whose running count is a multiple of 100. An identity sitting at three times its limit
produces a handful of lines per window rather than thousands, so judge severity by `count` and
not by how many lines you see.

### rate_limit.backend_error

The limiter could not reach Redis. Carries `scope` and `identity_type`. What happens to the
request depends on the scope, covered under failure modes below. These are worth alerting on:
while they appear, limits are not being applied.

### rate_limit.exemption_check_error

The exemption flag could not be resolved, usually the same outage. The request is treated as not
exempt and carries on to the limiter.

## Choosing Thresholds

Take the busiest legitimate identity over your observation window and leave headroom above it.
The `count` field on each crossing is the number to work from.

A limit applies per identity, not per deployment. A scope with a thousand active identities at
100/5m is not bounded at 100 requests overall, because each of those identities gets its own 100.
What the limit bounds is any single caller's share, which is what stops one tenant starving the
rest.

Watch out for treating equal numbers as equal cost. Most channel routes hand their work to Celery
and return in milliseconds, while a few run a model call inside the request. A threshold
calibrated against the cheap kind is not a safe ceiling for the expensive kind.

Every limit is an environment variable, so thresholds can move without a code change.

## Turning Enforcement On

Set `RATE_LIMIT_ENFORCE=True`. Requests over budget are refused from then on.

A refused caller gets:

- `429` with `{"detail": "Rate limit exceeded.", "available_in": <seconds>}` on the JSON surfaces
- the site's `429` error page on the public chat views, since a person reaches those in a browser
- `Retry-After`, in seconds, either way

Responses also carry `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`
whenever the limiter has counts to report. They are omitted when it does not, because there would
be nothing behind them.

One switch covers every scope. There is no per-scope enforcement flag, so calibrate all of them
before flipping it.

## Failure Modes

When the limiter cannot count at all, a scope either serves the request or refuses it. All of
them serve except `credentials`, which refuses: a counter nobody can read must not become a way
to brute force credentials unobserved. The cost of that choice is that while Redis is unreachable
and enforcement is on, OAuth token issuance stops. `RATE_LIMIT_ENFORCE=False` is the way out, and
it needs a restart.

## Exempting A Team

`flag_ignore_rate_limiting` is a Waffle flag, managed like any other (see
[Feature Flags](../admin_guides/feature_flags.md)).

Turn it on for specific teams to lift limits for one customer, for instance while you look into a
legitimate spike. Turn it on for everyone as a kill switch, which stops counting and enforcement
without a deploy or a restart.

The per-team form resolves the team from the request. Surfaces that only learn the team after the
view has done its own lookup, the provider webhooks among them, are not reached by it, and only
the everyone-on form applies there.

## What This Does Not Cover

Rate limiting bounds volume. It is not authentication, and it leaves these open:

- Requests rejected by the embedded widget's `X-Embed-Key` authentication are counted in no
  scope, because authentication runs before throttling.
- An AWS WAF rule, 2,000 requests per 5 minutes per address, is the outer backstop for
  single-source floods and is unaffected by anything here. It does not see a flood spread across
  many addresses.
- Limits are fixed per deployment. There is no per-team or per-plan tier.
