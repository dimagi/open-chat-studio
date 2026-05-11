# Unified Assessment — Overview

> A management-level overview of the proposed unification of OCS's two assessment systems. For the technical design, see [unified-assessment.md](./unified-assessment.md).

## TL;DR

OCS has two separate systems for evaluating chatbot quality — one for automated LLM/code judges, one for human review. They were built independently, share most of their underlying concepts, but don't talk to each other. We propose unifying them under a single user-facing concept — an **Assessment** — that lets a team set up "the thing I want to measure" once, and then attach automated judges, human reviewers, or both. This unblocks our top 2026 backlog priorities (concordance analysis, continuous monitoring, cross-system handoff) and removes the configuration fragmentation that currently makes common workflows painful.

## Today: two parallel systems

OCS currently ships two assessment subsystems, both in alpha/beta:

| System | Purpose | How it's used |
|---|---|---|
| **Evaluations** | Automated scoring via LLM judges or Python code | Bot Builder runs a judge over a curated test set or a saved filter of production sessions; results land in a results table per run |
| **Human Annotations** | Multi-reviewer rubric annotation | Team Lead sets up a queue with assignees and a rubric; reviewers work through the queue one item at a time, optionally flagging items for second review |

Both systems use the same kind of rubric language. Both produce structurally identical results (`{field_name: value}` dicts). Both attach to the same conversation sessions. Both are valuable on their own. But they're configured separately, run separately, and produce results in separate tables.

## The gap — what teams can't do today

The biggest pain isn't in either system on its own; it's in the space between them. Real workflows that customers ask for require crossing the boundary, and today that crossing is always manual.

**Concordance — "do humans agree with the judge?"** This is our **#1 backlog item** for 2026. To answer it today, a team has to: run the LLM judge → export CSV → run the human queue → export another CSV → match them in Excel by session ID → compute agreement metrics outside the platform. There's no in-platform answer to "is my LLM judge actually a reliable proxy for human judgment?" — the question every Bot Builder asks before trusting their evaluation pipeline.

**Continuous monitoring with human follow-up.** The natural production workflow is "let the LLM judge watch every session, flag low-quality ones, send them to a human reviewer." Today this requires configuring five or six separate places that don't know about each other: a saved filter, an evaluation config, a tag rule, an annotation queue, and a way to keep them in sync. Most teams give up and run only one half of this.

**User-feedback-driven review.** When a participant gives a 👎 on a chatbot's response, that's exactly the kind of signal a Reviewer wants in their queue. Today, user feedback is stored as a tag on a chat message; there's no path from "user gave negative feedback" to "this lands in someone's review queue." Teams have to write custom scripts.

**Trend tracking across both signal sources.** Once you have judges and humans both producing scores, you'd expect a single dashboard showing "quality over time, broken down by source." Today there are two dashboards (one per system) with no way to join them.

Of the nine cross-system items on our 2026 backlog, **four are explicitly about bridging this gap**. The current shape of the platform means each of them is a custom integration rather than a feature.

## The vision — one Assessment, many ways to score

We propose a single user-facing object: an **Assessment**. An Assessment expresses one signal a team wants to measure (e.g. "production response quality," "safety on user-generated topics," "regression vs the previous version"). Configuration is done once, on the Assessment, and the rest follows:

- **Pick what to measure.** Define a rubric — the structured fields a score has (e.g. `accuracy`, `helpfulness`, `safety`).
- **Pick what to assess.** A curated test dataset (offline) or a live filter of production sessions (continuous).
- **Pick who scores.** One or more automated scorers (LLM judges, Python code), one or more human review queues, or any mix.
- **Pick what happens next.** Routing rules express "if a judge scores something low, escalate it to a reviewer," "if a user gives negative feedback, add the conversation to a review queue," "if a reviewer flags an item as uncertain, get a second opinion."

From the user's perspective, the cognitive cost of "I want to add human review on top of my judge" drops from "configure six separate things that don't know about each other" to "add a Human Scorer to this Assessment and a routing rule." The judge and the human are now configured side-by-side, look at the same rubric, and produce comparable results.

Underneath, all scores — automated, human, or user-feedback — live in one shared store. Concordance is then a built-in tab on every Assessment with both kinds of scorer: "here's what the judge thought, here's what the humans thought, here's the agreement metric." No exports, no Excel matching, no custom scripts.

## User stories

These are the workflows the unified design supports. They're the input requirements; the design satisfies all ten.

### Development-time assessment

1. **Offline LLM-judge assessment to verify quality.** *As a Bot Builder, I want to run my chatbot over a curated test dataset and have LLM judges score each output, so that I can verify the quality of my changes before deploying.*

2. **Manual calibration of LLM judges.** *As a Bot Builder, I want to manually assess a sample of items that an LLM judge has scored, so that I can measure whether the judge is a reliable proxy for human judgment before I trust it.*

3. **Regression checks across versions.** *As a Bot Builder, I want to compare LLM-judge scores between a new chatbot version and a baseline version on the same dataset, so that I can catch regressions before deploying.*

### Production-time assessment

4. **Continuous LLM-judge monitoring on production.** *As a Bot Owner, I want LLM judges to automatically assess a sample of production conversations on an ongoing basis, so that I can monitor real-world quality without manual review of every interaction.*

5. **Human review queue, judge-flagged.** *As a Reviewer, I want to see a queue of production conversations that LLM judges flagged as low-quality or concerning, so that I can manually validate or correct the judge and triage real issues.*

6. **Human review queue, user-feedback-flagged.** *As a Reviewer, I want to see a queue of production conversations where users gave negative feedback, so that I can investigate and learn from real failures.*

### Cross-cutting analysis

7. **Concordance between humans and judges.** *As a Bot Builder or Team Lead, I want to see agreement metrics between human and judge scores on the same items, so that I can trust (or distrust) my automated evaluation pipeline and improve it.*

8. **Trend monitoring across assessments and over time.** *As a Bot Owner or Team Lead, I want a dashboard showing assessment trends across multiple assessments and over time on production, so that I can track quality progression and catch issues early.*

### Multi-reviewer workflows

9. **Inter-rater reliability.** *As a Team Lead, I want a configurable portion of review work to be assigned to multiple reviewers in parallel, so that I can measure inter-rater agreement and trust that human scores are well-calibrated.*

10. **Second-pass review for uncertain items.** *As a Reviewer, I want to flag items I'm uncertain about for a second-pass review by another reviewer, so that ambiguous cases get appropriate attention rather than being decided by a single fallible judgment.*

## What this unlocks — backlog items addressed

The 2026 assessment backlog has nine items. The unified design directly enables all of them:

| # | Item | How the unified design enables it |
|---|---|---|
| 1 | LLM eval vs human annotation concordance | Built-in tab on any Assessment with ≥2 scorer types |
| 2 | Auto-add new sessions to evaluation | Live-filter source streams in matching sessions |
| 3 | Import entire sessions into evaluations | Already supported as a session-granularity source |
| 4 | Evals tag sessions for filtering | Routing rule with tag-emission action |
| 5 | Auto-add sessions to review queue from eval tags | Within-Assessment escalation routing rule |
| 6 | Assign specific sessions to specific reviewers | Property of the Human Scorer |
| 7 | Export CSV with global session ID | Trivial in the unified export path |
| 8 | Import evals dataset into review queue | Add a Human Scorer to an automated-only Assessment |
| 9 | Import review items into evaluations | Add an Automated Scorer to a human-only Assessment |

In the current shape, items 1, 5, 8, and 9 require custom integration work. In the unified design, they become natural configuration choices on a single Assessment.

## Scope of this proposal

**In scope.** A single back-end model and configuration shape that supports all ten user stories, replaces the two existing user-facing concepts (Evaluation Config and Annotation Queue) with one (Assessment), and makes cross-system workflows configuration choices rather than integration work.

**Out of scope of this proposal (separate work).**
- Sequencing and migration plan from today's two systems to the unified one. Existing data is in alpha/beta — the migration cost is real but manageable.
- Detailed UI design. The model dictates what's *possible*; the UI design dictates what's *easy*. They're sequential.
- Dogfooding plan. Story 5 ("judge-flagged human review queue") is the natural integration test for whether the unification actually fixes the fragmentation pain — a known team or use case to validate against would help.

## Risks and dependencies

- **Alpha/beta migration.** Existing evaluation runs and annotation queues need a migration path or a wipe-and-reseed decision. The latter is simpler but loses data; the choice depends on whether any team is depending on alpha-era results.
- **User-feedback rework.** Today's 👍/👎 lives as tags on chat messages. The unified design retargets them as scores on the actual interaction (a "Trace"). Historical thumbs would be dropped during migration — acceptable given alpha status and low-volume usage.
- **Single feature flag.** The two systems are gated by separate feature flags today (one for evaluations, one for human annotations). The unified system uses a single flag, which means teams that have one system enabled today will need a coordinated rollout to get both.

## Success criteria

The unified design succeeds if:

1. A Bot Builder can set up "judge + human review on production with routing on flagged items" in one configuration screen, not five.
2. The concordance question ("does my judge agree with my humans?") has an in-product answer — no exports, no Excel.
3. The four cross-system 2026 backlog items (1, 5, 8, 9) ship as configuration choices, not as bespoke integrations.
4. Adding a new scoring kind in the future (e.g. a third-party safety classifier) is a matter of adding a scorer type, not standing up a third parallel subsystem.
