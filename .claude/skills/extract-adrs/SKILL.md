---
name: extract-adrs
description: Extract Architecture Decision Records from a stable design or spec document. Use when a design doc is finished and its decisions should be crystallised into citable ADRs at docs/adr/. Refuses to run on docs with `status: active` frontmatter. Pass the source doc path as the argument.
---

# Extract ADRs from a design doc

Drive a guided conversation that converts a stable design or spec document into multiple ADRs at `docs/adr/`, then either shrinks or deletes the source doc.

**Argument:** path to the source doc (e.g. `docs/superpowers/specs/2026-03-23-elevenlabs-voice-provider-design.md`).

**Format reference:** the canonical ADR template at `.claude/skills/extract-adrs/templates/adr-template.md` (and its twin at `docs/adr/_template.md`) defines the file structure. Status is an inline HTML `<span>` pill — there is no YAML frontmatter on ADR files. See `docs/adr/0000-record-architecture-decisions.md` for a worked example.

## Phase 1 — Gate check

1. Read the source doc's YAML frontmatter (the leading `---` block, if any).
2. Branch on `status`:
   - `active` → STOP. Tell the user the doc is marked active and refuse to proceed. Suggest they flip the status to `stable` if the design is settled.
   - `extracted` → Ask: "This doc has already been extracted. Re-extract?" — only proceed on explicit yes.
   - `stable` → continue to Phase 2.
   - No frontmatter at all → ask the user via `AskUserQuestion` to classify the doc. Options:
     - `Active — still evolving` (stop here; instruct user to add `status: active` frontmatter manually)
     - `Stable — ready for extraction` (continue to Phase 2)
     - `Pure plan — just delete it` (no decisions to extract; work has shipped; file is scaffolding)
     - `Mixed — let me think` (escape hatch — stop and let the user decide manually)

3. If the user picks "Pure plan — just delete it" → confirm once more, then `git rm <path>`, commit with message `docs: remove shipped implementation plan <basename>`, and exit.

## Phase 2 — Candidate decisions

1. Read the source doc in full.
2. Identify candidate decisions. A decision is something that:
   - Was a real choice (multiple plausible options existed).
   - Has consequences that constrain future work.
   - Would be useful to cite from code or PRs later.
3. Present a numbered list to the user. Each entry must include:
   - Short title (5–10 words, kebab-case ready).
   - One-line summary of the decision.
   - Source section(s) the decision came from (heading or line number).
4. Ask the user via `AskUserQuestion` (multi-select where possible) to:
   - Drop candidates that aren't real decisions.
   - Merge near-duplicates.
   - Split overly-broad candidates.
   - Rename if the title is poor.
5. Iterate until the user confirms the final list.

## Phase 3 — Per-ADR drafting

For each approved candidate **one at a time** (not batched):

1. (At the start of Phase 3 only) Allocate the next number:
   ```bash
   ls docs/adr/[0-9]*.md 2>/dev/null | grep -oE '[0-9]{4}' | sort -n | tail -n1
   ```
   Take the highest existing number, then reserve `N+1`, `N+2`, ... for this run. Show the user the planned numbering.

2. Read `.claude/skills/extract-adrs/templates/adr-template.md` once at the start of Phase 3 — use it as the model for every ADR you draft.

3. Draft the ADR. The structure follows the template exactly:
   - `# ADR-NNNN: Short title` heading.
   - `<span class="adr-status adr-status-{lowercase-status}">{UPPERCASE-STATUS}</span>` pill.
   - `<p class="adr-meta">Author: {user name} · Created: {today's date as YYYY-MM-DD}</p>` meta line.
   - Optional `Extends: [ADR-NNNN](NNNN-prior-title.md)` line (filled in during Phase 4).
   - `## Context` — paraphrase the source doc's framing of why this decision was needed. Keep tight: one or two paragraphs.
   - `## Decision` — one paragraph, lead with "We will…".
   - `## Consequences` — bullets, good and bad.
   - `## Alternatives considered` — bullets, one line each, name + rejection reason.

4. Use `AskUserQuestion` to confirm the ADR `status`:
   - `accepted` (default for extraction from shipped work).
   - `proposed` (decision is recorded but not yet implemented).
   - `rejected` (rare — captures a decision considered and turned down).
   - `draft` (still under discussion; unusual for extraction).

5. **Verify behavioral claims against the code** (for `accepted` ADRs). Every statement that asserts how the system *actually behaves* — data model, scoping (e.g. team-scoped vs global), defaults, control flow, named functions/modules/settings — must be checked against the implementation. Use Grep/Read to find the code and confirm each claim. The code is ground truth: where the source doc contradicts the implementation, correct the draft to match the code and flag the correction when you show the user. Cite the verifying symbol (e.g. `get_email_experiment_channel` in `apps/channels/channels_v2/email_channel.py`) in the ADR so future readers can re-verify. Skip this step for `proposed`/`draft` ADRs — there is nothing implemented to check yet.

6. Show the user the drafted ADR, noting any code-vs-doc corrections from step 5. Ask if they want edits before moving to the next candidate.

7. Do **not** write the file to disk yet — keep all drafts in conversation until Phase 5.

## Phase 4 — Cross-references

After all candidates are drafted:

1. Scan the drafts. When ADR B's Context paraphrases or relies on something ADR A decided, B "extends" A.
2. Build the proposed extension graph as a bulleted list:
   - `ADR-0042 extends ADR-0040`
   - `ADR-0043 extends ADR-0040, ADR-0042`
3. Show the user. Ask via `AskUserQuestion` if the graph is correct (Yes / Edit it).
4. Apply confirmed `Extends:` lines into each ADR draft (immediately below the meta line):
   ```markdown
   <p class="adr-meta">Extends: <a href="0040-...">ADR-0040</a></p>
   ```
   If extending multiple: separate with `, ` inside the same `<p class="adr-meta">`.

## Phase 5 — Write & wire up

1. Write each ADR file to `docs/adr/NNNN-kebab-title.md`. Write atomically per file using the Write tool.

2. Update the source doc per disposition rule:
   - **Design doc with surrounding narrative** (user stories, requirements, backlog mapping, etc.): replace the body (everything after the H1 title) with a brief overview paragraph plus a `## Decisions` section listing the extracted ADRs as links. Set `status: extracted` in frontmatter (add the frontmatter block if missing).
   - **Pure spec with no narrative beyond the decisions**: delete the file with `git rm`.
   - **Mixed / unclear**: ask the user via `AskUserQuestion` which disposition to apply.

3. Update `mkdocs.yml`:
   - Locate the `nav:` block, then the `Architecture:` → `Decisions:` subsection.
   - Append entries for each new ADR in numeric order, using 6-space indentation to match existing entries:
     ```yaml
           - NNNN Title: adr/NNNN-kebab-title.md
     ```

4. Update `docs/adr/index.md`:
   - Append a row per new ADR to the markdown table (after the existing rows, before EOF).
   - Use the format documented in the HTML comment at the top of the table:
     ```markdown
     | [NNNN](NNNN-kebab-title.md) | <span class="adr-status adr-status-{lowercase-status}">{UPPERCASE-STATUS}</span> | Short title |
     ```

5. Run the verification build:
   - `uv run --no-project zensical build --clean`
   - Expected: completes with no new errors. (Pre-existing warnings on unrelated docs are OK.) If errors, surface them to the user; do NOT auto-commit.

6. Print a summary:
   - "Wrote N ADRs: 0042 …, 0043 …, 0044 …"
   - "Source doc: <shrunk to pointer block | deleted | left unchanged>"
   - "Updated: mkdocs.yml (nav), docs/adr/index.md (table)"
   - "Next ADR number for future runs: {N+1}"
   - "Review the diff and commit when ready."

## Safety properties

- **Never commit from inside the skill.** The user reviews the diff and commits.
- **No file writes until Phase 5.** If interrupted between Phase 1 and Phase 5, nothing is on disk yet — safe to restart.
- **Atomic per file in Phase 5.** Each ADR file is written via a single Write call; the source doc is touched only after all ADR files are written.
- **If the build fails in Phase 5 Step 5**, surface the error and DO NOT proceed to print the summary as if successful. The user may need to roll back the ADR files (`git clean -f docs/adr/`) before retrying.

## When NOT to use this skill

- The source doc has `status: active`. Mark it `stable` first.
- The source doc is purely an implementation plan with no decisions. Use plain `git rm` + commit; don't run this skill.
- You want to write a single new ADR by hand. Just copy `docs/adr/_template.md` to `docs/adr/NNNN-kebab-title.md`, fill it in, and add nav + index entries manually.

## Worked example trigger

User: "Can you turn the elevenlabs voice provider spec into ADRs?"

Skill kicks off with the source path. Phase 1 detects no frontmatter, asks the user to classify, user picks "Stable — ready for extraction". Phase 2 proposes two candidate decisions ("Add ElevenLabs as a voice provider", "Map OCS voice config to ElevenLabs voice IDs"); user drops the second as too implementation-specific. Phase 3 drafts ADR-0001. Phase 4 has nothing to cross-reference. Phase 5 writes `docs/adr/0001-add-elevenlabs-voice-provider.md`, deletes the source spec, appends to mkdocs nav and the index. Build passes. User reviews and commits.
