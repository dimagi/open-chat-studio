# Auto Update Models - Decision Report

**Run date (UTC):** `YYYY-MM-DD HH:MM`
**Candidates considered:** `count`
**Outcome:** `PR opened | Nothing to do | Skipped - PR already open | Error`
**Migration:** the migration file you wrote, or `not needed - nothing changed`
**PR:** the PR URL, or `n/a`

## Per-model decisions

### `provider/model`

- **Verdict:** `Registered | Rejected | Needs follow-up`
- **Token limit:** the value, or `placeholder - needs verifying`
- **Pricing:** `applied | none available`
- **Reason:** one sentence
- **Ledger entry:** `deleted (registered) | rejected`
- **Source key:** the `litellm_key`

One block per `added[]` entry, all of them — this file is the run's audit trail.

## Deprecations

One line per `deprecated[]` entry: model, date, what you did.

## Removed upstream

One line per `removed[]` entry: model, what the provider's own docs say, and
either the replacement it was deleted in favour of or why it stays registered.

## Pricing

- **Rates changed:** `count` repriced, `count` backfilled
- **Corrections:** every change you made to what the script wrote, with the
  rate before and after and the source you checked it against. `none` if you
  accepted the script's output as it stood.
- **Unpriced:** one line per `unpriced[]` entry, or `none`.

## Notes

Sources consulted, edge cases, anything deferred.
