# ADR-0052: App-layer rate limiting via an in-house fixed-window core

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Barry Tandy · Created: 2026-08-11</p>

## Context

Issues #2140/#2349 require rate limiting across two enforcement surfaces: DRF API views and plain Django views (public chat, webhooks, credential endpoints). The #2140 spec proposed DRF's built-in throttles plus django-ratelimit for plain views. DRF's SimpleRateThrottle stores a per-key history list with a non-atomic read-modify-write, undercounting under concurrency. django-ratelimit has been dormant since 2023 and returns 403 without Retry-After on block, contradicting the spec's own header requirement. An AWS WAF rule (2,000 requests/5min per IP) remains the outer backstop.

## Decision

We will count in an in-house core (`apps/utils/rate_limit.py`): atomic fixed-window `INCR` with TTL against a dedicated Redis cache alias, consumed by a custom DRF throttle class and a plain-view decorator so both surfaces share one counter implementation, one keying scheme, and one response contract (`X-RateLimit-*` headers, 429 + `Retry-After` + `available_in` body). Scopes fail open on Redis errors (availability control); a scope can be marked fail-closed, a policy the planned `credentials` brute-force control will use. Enforcement is gated by `RATE_LIMIT_ENFORCE` (log-only first) and the `flag_ignore_rate_limiting` Waffle flag (per-team exemption; everyone-on is a global kill switch).

## Consequences

- One implementation to test and tune; both adapters emit identical headers and 429 bodies.
- Fixed windows permit a brief burst at window boundaries; accepted for atomicity and simplicity.
- Redis outage degrades to uncounted traffic on fail-open scopes, logged as `rate_limit.backend_error`.
- IP keying is only correct behind a proxy when `RATE_LIMIT_TRUSTED_PROXY_COUNT` is configured.

## Alternatives considered

- DRF built-in throttles: non-atomic counting undercounts under concurrency; memory grows with the rate.
- django-ratelimit for plain views: dormant project, 403-without-Retry-After block responses, key functions do not compose with team/API-key/session keying.
- GCRA (Zulip-style leaky bucket via Lua): eliminates boundary bursts and supports stacked rules; deferred as a future upgrade because fixed windows meet current requirements with less machinery.
