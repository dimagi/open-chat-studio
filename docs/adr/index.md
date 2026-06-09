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
| [0021](0021-invest-in-api-surface-not-readonly-role.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | Invest in API surface, not a read-only role |
| [0022](0022-url-path-api-versioning.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | URL-path API versioning, v1 frozen / v2 new |
| [0023](0023-rename-experiment-to-chatbot-in-v2.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | Rename experiment to chatbot in the v2 API |
| [0024](0024-inspect-denormalized-readonly-projection.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | inspect as a denormalized read-only projection |
| [0025](0025-inline-nested-resource-tree.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | Inline nested resource tree for the inspect payload |
| [0026](0026-identify-resources-by-primary-key.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | Identify v2-exposed resources by database primary key |
| [0027](0027-secrets-exclusion-via-allowlist-serializers.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | Secrets exclusion via per-resource allowlist serializers |
| [0028](0028-inspect-authorization-team-scoped.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | Inspect authorizes on chatbot view + team scope, not per-resource permissions |
| [0029](0029-download-whatsapp-inbound-attachments-in-hydration-stage.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Download WhatsApp inbound attachments in an overridden hydration stage |
| [0030](0030-email-channel-allowed-domains-global-setting.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Gate the email channel with a global allowed-domains setting |
| [0031](0031-collection-content-is-live-shared-resource.md) | <span class="adr-status adr-status-proposed">PROPOSED</span> | Collection content is a live shared resource |
| [0032](0032-server-side-jinja-template-validation.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Validate Jinja templates server-side by parsing the AST |
| [0033](0033-structured-runtime-jinja-error-messages.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Structured runtime Jinja error messages |
| [0034](0034-tiered-feature-deprecation-by-usage-audit.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Tiered feature deprecation gated by a usage audit |
| [0035](0035-read-only-mode-during-deprecation-window.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Read-only mode gates features during the deprecation window |
| [0036](0036-sunset-headers-and-410-for-retired-http-surfaces.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Sunset headers and 410 Gone for retired HTTP surfaces |
| [0037](0037-row-multiplying-filters-use-exists-not-distinct.md) | <span class="adr-status adr-status-accepted">ACCEPTED</span> | Row-multiplying list filters use EXISTS, not a blanket DISTINCT |