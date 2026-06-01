---
hide:
  - navigation
  - toc
---

# Architecture Decisions

This section captures architectural decisions made on Open Chat Studio as Architecture Decision Records (ADRs). See [ADR-0000](0000-record-architecture-decisions.md) for the introduction.

## Index

<!--
Row format for the /extract-adrs skill to append:
| [NNNN](NNNN-kebab-title.md) | <span class="adr-status adr-status-{lowercase-status}">{UPPERCASE-STATUS}</span> | Short title |

Where {lowercase-status} is one of: draft, proposed, accepted, rejected, superseded.
-->

| ADR | Status | Title |
|-----|--------|-------|
| [0000](0000-record-architecture-decisions.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Record architecture decisions |
| [0001](0001-use-anymail-webhook-for-email-ingress.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Use AWS SES + django-anymail signal handler for email ingress |
| [0002](0002-email-channel-slack-style-routing.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Slack-style routing priority chain for email channel |
| [0003](0003-email-thread-continuity-via-external-id.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Email thread continuity via ExperimentSession.external_id |
| [0004](0004-persist-inbound-email-attachments-in-handler.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Persist inbound email attachments in the webhook handler |
| [0005](0005-validate-inbound-email-attachments-by-content.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Validate inbound email attachments by content sniffing |
| [0006](0006-combine-email-reply-text-and-attachments.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Combine email reply text and attachments into a single message |
| [0007](0007-adopt-ty-as-python-type-checker.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Adopt ty as the Python type checker |
| [0008](0008-progressive-ty-rule-enablement.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Enable ty rules progressively from a baseline of all-ignored |
| [0009](0009-context-based-stateless-message-processing-pipeline.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Context-based stateless message processing pipeline |
| [0010](0010-exception-based-early-exit-with-guaranteed-terminal-stages.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Exception-based early exit with guaranteed terminal stages |
| [0011](0011-silent-pipeline-halt-via-earlyabort.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Silent pipeline halt via EarlyAbort |
| [0012](0012-score-value-layer-in-apps-assessments.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Lean Score value layer in apps/assessments |
| [0013](0013-dual-write-scores-from-evaluations-and-annotations.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Dual-write Scores from evaluations and annotations |
| [0014](0014-minimal-read-side-concordance-view.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Minimal read-side concordance view backed by Score |
| [0015](0015-human-annotations-app-with-queue-item-annotation-aggregate-model.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Dedicated human_annotations app with queue/item/annotation/aggregate model |
| [0016](0016-authoritative-annotation-for-multi-reviewer-consensus.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Authoritative annotation for multi-reviewer consensus |
| [0017](0017-eager-aggregation-of-submitted-annotations.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Eager per-submission aggregation into a per-queue record |
| [0018](0018-scope-team-querysets-by-fk-not-slug-join.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Scope team querysets by FK identity, not slug join |
| [0019](0019-poll-source-experiments-to-auto-populate-eval-datasets.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Poll source experiments to auto-populate evaluation datasets |
| [0020](0020-delta-evaluation-runs-scoped-to-appended-messages.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Delta evaluation runs scoped to newly appended messages |
