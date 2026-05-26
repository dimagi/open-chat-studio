---
status: extracted
---

# Email Channel — Design Document

Add email as a messaging channel in Open Chat Studio. Inbound mail arrives via an ESP webhook and runs through the existing `channels_v2` pipeline; outbound replies are threaded back to the sender. The architectural decisions that came out of this design have been crystallised as ADRs — this document is now an index pointing to them.

## Decisions

- [ADR-0001: Use AWS SES + django-anymail signal handler for email ingress](../adr/0001-use-anymail-webhook-for-email-ingress.md)
- [ADR-0002: Slack-style routing priority chain for email channel](../adr/0002-email-channel-slack-style-routing.md)
- [ADR-0003: Email thread continuity via ExperimentSession.external_id](../adr/0003-email-thread-continuity-via-external-id.md)
