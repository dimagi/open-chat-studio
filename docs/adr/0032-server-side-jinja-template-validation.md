# ADR-0032: Validate Jinja templates server-side by parsing the AST

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-04</p>

## Context

The Email and Template pipeline nodes accept Jinja templates authored by users. Syntax errors were only discovered at run time, deep inside a trace, with no edit-time feedback. We render with `SandboxedEnvironment`, so any validation must use the same sandbox to give faithful results — undefined-variable errors are only knowable at run time and cannot be flagged early, but pure syntax errors can.

A second-class authoring path also exists: pipelines can be updated through the API without the editor, so editor-only validation would leave a gap.

## Decision

We will validate Jinja template syntax server-side by parsing the AST (never rendering), at two layers:

- **Edit-time** — a team-scoped `POST validate-jinja/` endpoint (gated by `login_and_team_required` + `pipelines.view_pipeline`) parses the template via `SandboxedEnvironment.parse()` and returns structured `{line, column, message, severity}` errors. A CodeMirror `linter()` extension in the `jinja_template` widget calls it on edit and renders inline diagnostics. The endpoint additionally runs djlint HTML linting (severity `warning`) behind a curated rule allowlist (`H020, H021, H025, T027, T034`); all other djlint rules are dropped as noise for template fragments.
- **Save-time** — a shared Pydantic `field_validator` on the template fields of both nodes calls the same parse helper, raising `invalid_jinja_syntax`. This is a backstop that catches syntax errors regardless of authoring path.

All parsing happens in Python; the client never parses Jinja, so editor feedback matches runtime behaviour exactly.

## Consequences

- Syntax errors surface inline while typing and again on save, instead of only at run time.
- The endpoint and the save-time validator share one parse helper, so the two layers cannot diverge in what they accept.
- Undefined-variable errors are still invisible until run time — parsing cannot see them (handled at run time, see ADR-0033).
- djlint operates on files, so each HTML-lint request writes the template to a temp file (RAM-backed where available) and cleans it up — a small per-request cost accepted for the warnings it surfaces.
- The endpoint carries its own input guardrails (50,000-char cap, a `checks` selector for `jinja`/`html`) since it takes raw request bodies.
- Adds `@codemirror/lint` as a frontend dependency.

## Alternatives considered

- **Client-side Jinja parsing** — rejected; a JS parser would drift from the Python `SandboxedEnvironment`, breaking parity between editor feedback and runtime.
- **Validate on `render()` instead of `parse()`** — rejected; template variables are only available at run time, so rendering at edit/save time would raise spurious undefined-variable errors.
- **Editor-only validation** — rejected; API-driven pipeline updates bypass the editor, so the Pydantic backstop is needed to close the gap.
- **Full djlint rule set** — rejected; most rules (DOCTYPE, `<title>`, lang attributes, `url_for`) assume a complete HTML document and only produce noise on template fragments.
