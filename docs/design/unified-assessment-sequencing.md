# Unified Assessment — Sequencing & Options

> Companion to [unified-assessment.md](./unified-assessment.md) (technical design) and [unified-assessment-overview.md](./unified-assessment-overview.md) (management overview). This document discusses *how* and *when* we get from today's two systems to the unified design, given near-term team priorities.

## Context

**Near-term team priorities**
1. **Live evals** — continuous LLM-judge monitoring on production sessions (Story 4 in the design doc). No infrastructure exists today; one-shot filter-based dataset creation is the closest analogue.
2. **Concordance** between automated evals and human assessments (Story 7). Today this requires two CSV exports + Excel matching; no in-product answer.

**Constraints**
- Both existing systems (`apps/evaluations/` and `apps/human_annotations/`) are in alpha/beta. Real users exist; data may or may not be wipeable (open question 1 in the design doc).
- The full unified design is a several-month build. Team can't be blocked that long on these two priorities.
- A short-term unblocker is on the table — including throwaway code that gets deleted when the unified system lands.

This doc presents four viable approaches, the trade-offs of each, and a recommendation. Timelines below assume AI-assisted development on a single track — implementation compresses substantially, though design, review, and integration time compress less.

## What "done" looks like for each priority

To compare options honestly, we need to be specific about what shipping each priority means.

**Live evals — minimum bar**
- A team can configure: "run *this* eval on production sessions matching *this* filter, on an ongoing basis."
- Results land somewhere queryable and visible in-product.
- No expectation of sub-minute latency; per-session-end evaluation is acceptable.

**Concordance — minimum bar**
- Given an evaluation run and a human annotation queue on the same set of sessions, the team can see in-product: per-field side-by-side scores and a basic agreement metric.
- Doesn't have to be a single configuration object; can be a separate "compare" tool.
- Doesn't have to support all field types in v1 — covering numeric and categorical is enough.

These bars are *much* lower than the full unified design's promise. That's deliberate: the question this doc answers is "what's the fastest path to these bars without compromising the strategic direction."

## The four options

### Option 1: Full greenfield, wipe alpha data

Build the unified system as designed. Wipe existing alpha/beta data. No throwaway. Live evals and concordance land when the unified system lands.

**Pros**
- Cleanest end-state. No legacy debt, no migration logic, no parallel codebases at any point.
- Lowest total engineering cost — one design, one build.
- Design decisions enforced consistently from day one.

**Cons**
- Live evals and concordance are blocked until the full system ships (estimate: 6–12 weeks). The two priorities are the *last* things to land, not the first.
- Wipes alpha data. Teams currently using either system lose their history. May be a customer conversation we don't want to have.
- High-risk single delivery — late-stage discoveries can derail the whole thing.

**Best fit if:** alpha data is wipeable, the team can tolerate the wait, and we want a single clean delivery.

### Option 2: Full greenfield, with data migration

Same as Option 1 but write migration logic to preserve existing evaluation runs and annotation queues.

**Pros**
- Continuity for current users. No data lost.
- Same clean end-state as Option 1.

**Cons**
- All the cons of Option 1, plus migration cost. Migration shapes are non-trivial: today's `EvaluationResult` becomes both `AutomatedResult` *and* N `Score` rows; today's user-feedback tags retarget from `ChatMessage` to `Trace`; schemas materialise into a new catalogue. Each transform is a deliberate engineering task.
- Migration is mostly throwaway code that runs once and gets deleted — same cost trade-off as Option 4 (below), without the unblocking benefit.

**Best fit if:** alpha data preservation is a hard requirement *and* the team can tolerate the wait. In practice, given alpha/beta status, this option is hard to justify.

### Option 3: Incremental — Score-first, bottom-up

Build the unified system in slices, starting from the bottom layer (the `Score` table). Top-level configuration unification (`Assessment` as a user concept) is deferred to a later phase.

Order of slices:
1. **Score table**, plus dual-writes from today's `EvaluationResult` and `Annotation` so both systems populate it without removing the existing tables. The user-facing concepts (`EvaluationConfig`, `AnnotationQueue`) stay unchanged.
2. **Concordance tab** built on `Score`. Ships as soon as slice 1 is in production and both sides are writing scores.
3. **Live evals as new infrastructure** that writes `Score` directly. Lives alongside today's batch eval machinery; new feature, not a refactor.
4. **Configuration unification** (Assessment + Source + Scorer) folds the existing user-facing models into the new shape. Existing `EvaluationConfig` / `AnnotationQueue` either migrate or are deprecated.

**Pros**
- Each slice ships independent value. Team is unblocked at slice 2 (concordance) and slice 3 (live evals).
- Slices 1–3 don't disturb existing user-facing concepts — low rollout risk.
- The bottom-up unified model is the durable architecture; even if slice 4 stalls, slices 1–3 are still on the strategic path.

**Cons**
- Two configuration concepts (`EvaluationConfig`, `AnnotationQueue`) coexist for a long time. Users see the old UI; the unification benefit at the configuration level is deferred.
- Dual-writes in slice 1 are extra engineering for a transitional state. Some Score-shape decisions (e.g. target type for offline evals) constrain how existing data plugs in.
- Risk of stalling between slices 3 and 4 — slice 4 is the largest and offers the smallest visible benefit at delivery time, which can make it a poor candidate for prioritisation.

**Best fit if:** concordance and live evals are the urgent deliverables and we're willing to live with two config UIs for a while.

### Option 4: Throwaway unblocker first, then full unified greenfield

Ship tactical short-term solutions for both priorities on top of today's two systems, then start fresh on the unified design once the team is unblocked.

**Throwaway live evals** — a scheduled Celery task on top of existing `EvaluationConfig`: "run this eval on sessions matching this saved filter, every N hours." Sits on existing eval machinery. Estimate: ~1 week. Throwaway cost: the scheduling + filter-to-sessions glue (~few hundred lines).

**Throwaway concordance** — an in-product report that joins `EvaluationResult` + `Annotation` by session ID, with heuristic field-name matching (string equality). Computes basic agreement (correlation for numeric, percent-agreement for categorical). Estimate: 3–5 days. Throwaway cost: the report view and the join logic.

After both are shipped, start fresh on the unified design (effectively Option 1 or 2).

**Pros**
- Fastest possible unblock — both priorities ship in ~2 weeks total.
- Buys design time for the strategic work — the unified system can be built without time pressure.
- Limits risk: the throwaway is small enough that *if* the unified system slips, we still have something in production.

**Cons**
- Real cost of writing throwaway code that ships, runs, and gets deleted. Engineering effort that doesn't compound.
- The dreaded "throwaway becomes permanent" pattern. Once a feature is in production and users depend on it, the cost of replacement isn't free even if the code is.
- Two parallel codebases for a while (throwaway live-evals + unified live-evals during the rebuild).
- Throwaway concordance with string-matched fields is *lossy* — it will give wrong answers when field names match but don't represent the same thing. Users will notice and lose trust. Possibly worse than no in-product concordance.

**Best fit if:** the team is urgently blocked, the cost of the wait outweighs the cost of throwaway, and we accept some short-term lossiness in concordance.

## Decision matrix

| | Time-to-live-evals | Time-to-concordance | Throwaway cost | End-state quality | Risk |
|---|---|---|---|---|---|
| **1** Greenfield, wipe | 6–12 weeks | 6–12 weeks | None | High | High (single big bet) |
| **2** Greenfield, migrate | 8–16 weeks | 8–16 weeks | Migration logic | High | High |
| **3** Incremental, Score-first | ~6–8 weeks | ~3–4 weeks | None | High (eventually) | Medium (slice 4 stall) |
| **4** Throwaway then greenfield | ~1 week | ~1 week (lossy) | Both throwaways | High | Medium (throwaway permanence) |

(Estimates assume AI-assisted development on a single track. They're shown so options can be compared against each other, not as commitments.)

## Recommendation

**Option 3 (Incremental, Score-first)** is the recommended path. Concordance lands in ~3–4 weeks on the right foundation; live evals follow in ~6–8 weeks as new infrastructure that writes directly to the unified score store. No throwaway code, every slice ships value, and the bottom-up structure is the durable architecture even if the top-level unification (slice 4) ever stalls.

**Option 4 is *not* recommended** even though it ships fastest. The throwaway concordance is lossy (heuristic field-name matching gives wrong answers when names match but mean different things), and the time saved over Option 3 is now ~2–3 weeks — not enough to justify shipping a feature users will distrust. If live evals alone is urgent, the live-evals throwaway can be lifted out of Option 4 as a tactical one-week patch on top of Option 3's slice 1, but that decision can be made later if needed.

**Option 1 is viable** if alpha data is confirmed wipeable and the team can absorb a 6–12 week wait with nothing landing in the meantime. It's the cleanest engineering outcome but the worst near-term outcome — concordance and live evals are the *last* things to ship rather than the first.

**Option 2** has no clear use case given alpha/beta status.

## Decision inputs we still need

Before committing to any option, two answers from outside this doc:

1. **Is alpha data wipeable?** This is open question 1 from the design doc. A "yes" makes Option 1 viable and removes migration cost from any option. A "no" rules out Option 1 and makes migration cost a permanent line item in Option 2.
2. **What's the live-evals deadline?** "ASAP" vs "this quarter" vs "no firm deadline" changes the answer materially. Option 3 ships live evals in ~6–8 weeks; Option 4 ships a throwaway in ~1 week. That gap matters only if there's a hard date inside it.

A small data point each from product / customer-facing teams would let us commit to a specific path.

## Next steps if we proceed

Whichever option is picked, the immediate next steps are independent:

- Confirm alpha-data wipeability.
- Pin down live-evals urgency with product.
- For Option 3: design slice 1 (Score table + dual-writes from existing `EvaluationResult` and `Annotation`) in enough detail to estimate. This is also the first work item under Option 4's "then full unified" phase, so the design effort isn't wasted either way.
- For Option 4: scope the throwaway live-evals MVP — what's the minimum a team needs to consider it "live evals"?
