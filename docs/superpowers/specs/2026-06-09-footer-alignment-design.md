# Footer alignment & settings-driven legal URLs

## Goal

Bring the in-app (authenticated) footer visually closer to the pre-login marketing
footer, and make the legal links (terms, privacy, acceptable use) on the pre-login
site read from Django settings instead of hardcoded Dimagi URLs. Add an
acceptable-use URL to settings.

## Scope

In:
- Add `ACCEPTABLE_USE_POLICY_URL` to `PROJECT_METADATA` and `.env.example`.
- Rebuild the in-app footer as a compact two-row layout echoing the pre-login style.
- Point the pre-login footer's legal links at settings, hidden when unset.

Out:
- Marketing-only columns (Use Cases, Community, etc.) are not added to the in-app footer.
- Non-legal pre-login links (Sign In, Docs, GitHub, Dimagi links) stay as-is.

## 1. Settings

`config/settings.py` — `PROJECT_METADATA` gains a third legal URL, following the
existing pattern:

```python
"TERMS_URL": env("TERMS_URL", default=""),
"PRIVACY_POLICY_URL": env("PRIVACY_POLICY_URL", default=""),
"ACCEPTABLE_USE_POLICY_URL": env("ACCEPTABLE_USE_POLICY_URL", default=""),
```

`.env.example` — add under the existing "Optional terms and policy URLs" block:

```
# ACCEPTABLE_USE_POLICY_URL=
```

All three default to empty string. The `project_meta` context processor already
copies `PROJECT_METADATA` wholesale, so the new key is automatically available to
templates as `project_meta.ACCEPTABLE_USE_POLICY_URL`.

### Behavior change (call out in PR)

Defaults are empty, and links are hidden when unset. The pre-login footer currently
shows the Dimagi legal links unconditionally. After this change the Dimagi
production/marketing deploy **must set** `TERMS_URL`, `PRIVACY_POLICY_URL`, and
`ACCEPTABLE_USE_POLICY_URL` env vars, or those links disappear from the pre-login
footer.

## 2. In-app footer

`templates/web/components/footer.html` — rebuilt as a compact, two-row footer using
daisyUI classes (`bg-base-200` / `text-base-content`) so it follows light/dark theme.

Layout:
- **Row 1**: brand name (`project_meta.NAME`) on the left; on the right —
  Documentation (`project_meta.DOCS_URL`), GitHub (existing repo link
  `https://github.com/dimagi/open-chat-studio`), and the existing language selector
  (`web/components/language_select.html`).
- **Divider**.
- **Row 2** (smaller text): copyright `© <current year>` on the left; on the right —
  "Terms of use", "Privacy Policy", "Acceptable Use", each rendered only if its
  corresponding settings URL is non-empty, separated by a bullet/middot.

Notes:
- No tagline (the `DESCRIPTION` line is dropped per design review).
- Preserve the existing current-year JS that sets the copyright year.
- Terms link keeps `target="_blank"` (consistent with current behavior).

## 3. Pre-login footer

`templates/prelogin/base.html` — in `.footer-bottom`, replace the three hardcoded
`dimagi.com` hrefs with settings values, each link wrapped so it only renders when
its URL is set. Labels and ordering (PRIVACY · TERMS · ACCEPTABLE USE POLICY) and the
`target="_blank" rel="noopener"` attributes are unchanged.

- PRIVACY → `project_meta.PRIVACY_POLICY_URL`
- TERMS → `project_meta.TERMS_URL`
- ACCEPTABLE USE POLICY → `project_meta.ACCEPTABLE_USE_POLICY_URL`

## Testing

- Manual: render both footers with the env vars set and unset; confirm links appear
  only when configured and the in-app footer respects light/dark theme.
- Confirm the in-app footer is visibly more compact than the previous block.
