# Upgrading django-allauth

OCS overrides around 30 of allauth's templates (`templates/account/`, `templates/mfa/`,
`templates/socialaccount/`). The overrides are full rewrites — OCS's design system,
`render_field` tags and `prelogin/auth_base.html` — not tweaks of allauth's markup. That
has one consequence worth remembering on every upgrade:

**Template fixes shipped upstream never reach OCS.** When allauth adds a context variable,
gates a link behind a new flag, or renames something, the overrides keep rendering the old
shape. Django resolves an unknown variable to the empty string, so the failure is silent:
a section of the page simply goes blank. `mfa/totp/activate_form.html` rendered a blank
"manually enter this secret" step this way, because it read `authenticator.wrap.secret`
while allauth's view only puts the secret on the form.

## After bumping the version

```bash
uv run python scripts/diff_allauth_overrides.py
```

The script compares each override against the installed upstream template and reports
functional drift — URLs, form fields, form actions and context variables upstream uses that
the override does not, plus anything the override reads that upstream never mentions. It
also lists which upstream templates OCS does *not* override (those pick up upstream fixes
for free) and which OCS templates have no upstream counterpart.

Findings are candidates, not bugs. Triage each against the upstream view:

- **upstream-only URL** — usually a form action OCS posts to implicitly, or a feature OCS
  doesn't enable (e.g. webauthn). Check whether the link is reachable another way.
- **upstream-only form field** — a field allauth now renders that OCS's form markup skips.
- **OCS-only context variable** — the riskiest kind. Confirm the name is really in the
  view's context; if it isn't, that part of the page renders nothing.

Also read allauth's `ChangeLog.rst` for the versions you skipped: new settings often come
with new context variables that the overrides have to honour. `MFA_RECOVERY_CODES_SHOW_ONCE`
(65.16.0) is the worked example — turning it on required
`templates/mfa/recovery_codes/index.html` to respect `can_view_codes`,
`can_download_codes` and `can_generate_codes`, or the page would have offered a download
link that 403s.

## Settings worth knowing about

- `MFA_RECOVERY_CODES_SHOW_ONCE` (on) — recovery codes are viewable once, right after
  generation. The view page then masks them and only offers regeneration.
- `ALLAUTH_RATE_LIMIT_IPV6_PREFIX` (default `/64`) — rate limiting buckets IPv6 clients by
  network prefix rather than address, so a client can't rotate addresses to bypass the
  login throttle. Widen it if IPv6 users on shared prefixes report being throttled.
- `allauth.mfa.signals.authentication_failed` is logged by
  `apps/users/signals.py` as `mfa.authentication_failed` on the `ocs.users` logger, with
  user, authenticator type and client IP. The account-level `*_code_rejected` signals
  cannot fire in OCS: login-by-code, password-reset-by-code and email-verification-by-code
  are all disabled.
