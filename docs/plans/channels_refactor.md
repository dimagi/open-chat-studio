---
status: extracted
---

# Channel Refactoring: Context-Based Stateless Processing Architecture Analysis

**Date**: 2025-12-17

This document was the design analysis for the channels v2 refactor. The core architectural
decisions have been crystallised into ADRs; the implementation detail (stage code, rollout
status, testing strategy) lives in `apps/channels/channels_v2/`.

## Decisions

- [ADR-0009](../adr/0009-context-based-stateless-message-processing-pipeline.md) — Context-based stateless message processing pipeline
- [ADR-0010](../adr/0010-exception-based-early-exit-with-guaranteed-terminal-stages.md) — Exception-based early exit with guaranteed terminal stages
