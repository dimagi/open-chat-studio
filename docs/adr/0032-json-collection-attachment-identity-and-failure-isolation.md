# ADR-0032: Attachment-level document identity and per-attachment failure isolation

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-03</p>

<p class="adr-meta">Extends: <a href="0031-json-collection-document-source-loader.md">ADR-0031</a></p>

## Context

A JSON feed item can carry several attachments (PDFs etc.), each fetched and text-extracted independently. The framework identifies each `Document` by a stable string (`get_document_identifier`) and decides re-fetch via `should_update_document`; `DocumentSourceManager` uses these to add, update, and remove `CollectionFile`s across syncs. We had to choose what counts as a distinct document and how one bad attachment affects the rest of a sync.

## Decision

We will make each fetchable attachment its own `Document`, identified by its attachment `link`, and isolate per-attachment failures.

- An item with one or more attachments carrying a `link` yields one `Document` per such attachment, identified by the attachment `link` (stored in both `link` and `source` metadata), with item-level metadata merged in.
- An item with no fetchable attachments yields a single fallback `Document` whose content is the item `title` and whose identifier is the item `URI`. An item with neither `title` nor `URI` is skipped with a log line.
- A failed attachment fetch or text-extraction is logged and skipped; no fallback document is emitted for that attachment. Other attachments of the same item still yield.
- Re-fetch is decided by comparing the item-level `date` against the stored document's `date`: equal dates skip the update; otherwise (including a missing date on either side) the base re-fetch rule applies.

## Consequences

- Attachment identity is stable across syncs: a transient failure produces no document that sync and retries the next sync, without churning identifiers.
- An item whose every attachment fails yields zero documents that sync; its content appears only once a fetch succeeds.
- Change detection relies on the feed populating `date`; feeds without it re-fetch every sync.

## Alternatives considered

- **One Document per item** (concatenating attachments) → rejected; per-attachment documents give downstream RAG finer citation and isolate a single bad attachment.
- **Falling back to a `URI`-keyed document when an attachment fetch fails** → rejected; the fallback's identifier races the attachment's `link` identifier on later syncs, causing add/remove churn.
- **Content hashing for change detection** → rejected; the item-level `date` is cheaper and authoritative for this shape.
