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
