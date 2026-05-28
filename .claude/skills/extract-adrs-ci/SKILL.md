---
name: extract-adrs-ci
description: Autonomous (headless/CI) variant of extract-adrs. Extracts ADRs from a single source doc with no human interaction, opens a PR, and never merges. Invoked in GitHub Actions as `/extract-adrs-ci <source-doc-path>`. Do NOT use this interactively — use `/extract-adrs` instead.
---

# Extract ADRs autonomously (CI)

Headless variant of the `/extract-adrs` skill. Runs inside `anthropics/claude-code-action` on a nightly cron with no human present. Processes exactly ONE source doc (passed as the argument), opens a pull request for human review, and never merges.

**Argument:** path to one source doc, e.g. `/extract-adrs-ci docs/plans/human-annotations.md`.

**Format reference:** `.claude/skills/extract-adrs/templates/adr-template.md` defines the ADR file structure (inline `<span class="adr-status adr-status-{status}">` pill — no YAML frontmatter on ADR files). See `docs/adr/0000-record-architecture-decisions.md` for a worked example. The index row format is documented in an HTML comment in `docs/adr/index.md`.

## Hard rules (autonomous safety)

1. **No `AskUserQuestion`, ever.** There is no human. Every decision is a deterministic policy below.
2. **Never merge.** Commit to the current branch, push, open a PR labelled `claude`. Human review at the PR is the only gate to `main`.
3. **One doc per run.** Only process the single doc given as the argument.
4. **Delete the source after extraction.** The original narrative is preserved in git history and visible in the PR diff, so no shrink-to-pointer is needed.
5. **When in doubt, don't.** If the doc isn't clearly extractable, mark the tracking-issue task `blocked:` with a reason and exit cleanly — do not guess.

## Phase 1 — Gate check

Read the source doc's YAML frontmatter and assess its nature:

- `status: stable` → proceed to Phase 2 (extract, then delete source).
- `status: active` or `status: extracted` → SKIP. Mark the tracking-issue task `blocked: doc is <status>, not stable` and exit.
- **Confident pure implementation plan** (any of: filename ends `_plan`/`_testing_plan`, a `.py` example file, or the body is a plan with a `REQUIRED SUB-SKILL: ...executing-plans` header and task checklists but no decision rationale) → `git rm` the file, then jump to Phase 5 step "commit & PR" (no ADRs to write). State clearly in the PR body that this was a pure-plan deletion.
- Anything else (no frontmatter, ambiguous, mixed) → SKIP. Mark the tracking-issue task `blocked: not marked stable; needs human classification` and exit. Do NOT attempt extraction on an unmarked doc.

## Phase 2 — Candidate decisions (no human loop)

1. Read the source doc in full.
2. Identify the genuine architectural decisions — choices where multiple options existed, that have lasting consequences, and that are worth citing later. Apply two filters: **(a) avoid the trivial** — skip mechanical or easily-reversed minutiae; **(b) split-vs-fold** — a candidate is its own ADR only if you'd plausibly supersede or revise it *independently* of the others. A choice that exists only as a forced consequence of a bigger decision (a stub library dictated by the type-checker you picked, a serializer forced by your framework) is NOT its own ADR — fold it into the parent's Decision/Consequences/Alternatives, and use `extends:` to link related-but-separate ADRs. No human will catch over-splitting here, so apply this deliberately.
3. You decide the final list yourself (no confirmation step). For each decision, record a one-line justification for WHY it qualifies as a decision and which source section it came from. **This reasoning goes into the PR body** so the human reviewer can audit your curation.

## Phase 3 — Draft ADRs

Allocate numbers: run `ls docs/adr/[0-9]*.md 2>/dev/null | grep -oE '[0-9]{4}' | sort -n | tail -n1`, take the highest, reserve N+1, N+2, … for this run.

Read `.claude/skills/extract-adrs/templates/adr-template.md` and draft each ADR to match it:
- `# ADR-NNNN: Short title`
- `<span class="adr-status adr-status-accepted">ACCEPTED</span>` — default status `accepted` (extraction is of shipped/settled work). Use `proposed` only if the source explicitly says the decision is not yet implemented.
- `<p class="adr-meta">Author: Open Chat Studio · Created: {today YYYY-MM-DD}</p>`
- `## Context`, `## Decision` (lead with "We will…"), `## Consequences`, `## Alternatives considered`.

**Content discipline.** ADRs are immutable once accepted, so anything that decays must stay out. Inside the body: **cut** file paths, `file.py:lineno` references, private helper names (`_foo`, internal underscore-prefixed methods), code-walk paraphrases of the implementation, exact log strings, and dated migration filenames. **Keep** identifiers the decision creates as public contracts: model + field names, DB constraint / index names, enum values, waffle flag IDs, URL routes and query-parameter surfaces, and ORM lookup paths when the join *is* the decision (e.g. "filter through `run.config`, not `evaluator`"). Deciding heuristic: "If I rename this tomorrow, do I need a migration or just a refactor?" Migration → keep. Refactor → cut. No human edits the draft between you and the PR, so apply this deliberately.

**Verify every behavioral claim against the code (mandatory for `accepted` ADRs).** Before finalising each ADR, locate the implementation for every assertion about how the system behaves — data model, scoping (team-scoped vs global), defaults, control flow, named functions/modules/settings — using Grep/Read. The code is ground truth: where the source doc contradicts the implementation, write what the code actually does and record the correction in the PR body. Record the verifying symbol (file + function) in the PR body for the human reviewer; do NOT pin file paths or `:lineno` refs into the ADR body itself (the ADR is immutable and those references rot on the next refactor — see Content discipline above). If a load-bearing claim cannot be located or confirmed in the code, do NOT assert it — soften the ADR to what you can verify, or, if the whole decision hinges on the unverifiable claim, mark the tracking-issue task `blocked: unverifiable against code` and skip that ADR. Skip verification for `proposed` ADRs (nothing is implemented yet).

## Phase 4 — Cross-references

Infer `Extends:` relationships between the ADRs you drafted (when one builds on another's decision). Add the `<p class="adr-meta">Extends: <a href="NNNN-...">ADR-NNNN</a></p>` line below the meta line. Note the inferred graph in the PR body. No confirmation step.

## Phase 5 — Write, wire up, PR

1. Write each ADR file to `docs/adr/NNNN-kebab-title.md`.
2. `git rm` the source doc (skip if Phase 1 already removed a pure plan).
3. Update `mkdocs.yml`: append each new ADR under the `Architecture:` → `Decisions:` nav block in numeric order (6-space indent), e.g. `      - NNNN Title: adr/NNNN-kebab-title.md`.
4. Update `docs/adr/index.md`: append a table row per new ADR using the documented format `| [NNNN](NNNN-kebab-title.md) | <span class="adr-status adr-status-{lowercase}">{UPPERCASE}</span> | Short title |`.
5. Verify the build: `uv run --no-project zensical build --clean`. If it errors, do NOT open a PR — comment the build error on the tracking issue, mark the task `blocked: build failed`, and exit.
6. Commit: `git add docs/adr/ mkdocs.yml <source-doc-path>` (the source path captures the `git rm`). Commit message: `docs: extract ADRs from <basename>` (or `docs: remove shipped implementation plan <basename>` for the pure-plan path).
7. Push the current branch and open a PR: `gh pr create --label claude --title "..." --body "..."`. The PR body MUST include: the list of ADRs written, the per-ADR justification from Phase 2, the inferred extends-graph from Phase 4, and the source-doc disposition (extracted+deleted, or pure-plan deleted).
8. Check off the corresponding `- [ ] /extract-adrs-ci <doc>` line in the tracking issue (derive the issue number from the current branch name `claude/<N>-...` or the invoking context) using `gh issue edit`, and add a brief `gh issue comment` summarising the run.

## Differences from the interactive `extract-adrs` skill

| Interactive (`extract-adrs`) | Autonomous (`extract-adrs-ci`) |
| --- | --- |
| `AskUserQuestion` at each decision | No prompts; deterministic policy; reasoning goes in PR body |
| Classifies unmarked docs with the user | Only `stable` docs or confident pure plans; else `blocked:` |
| Disposition: shrink / delete / ask | Always delete the source (git history is the record) |
| Never commits | Commits, pushes, opens a PR (never merges) |
| Human reviews inline | Human reviews the PR |
