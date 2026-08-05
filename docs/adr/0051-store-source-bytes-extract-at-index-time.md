# ADR-0051: Store the bytes a source served; extract text at index time

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-08-05</p>

## Context

Document source loaders returned LangChain `Document` objects, whose `page_content` is text.
For the GitHub and Confluence loaders that is all the API offers. The JSON collection loader
is different: it downloads real files — mostly PDFs — and was extracting their text at sync
time, storing the extracted string as the `File`'s content.

The filename, meanwhile, comes from the source (`report.pdf`). So the stored file was text
under a `.pdf` name, and downloading it produced a `.pdf` no reader could open. The sniffed
content type matched the stored text rather than the document, so nothing downstream could
tell the two apart either.

Extraction at sync time also fixes the choice of parser at the wrong moment. The feed's
`file_type` field is a third party's claim, and the readers (`apps/documents/readers.py`) are
the component that knows how to pick a parser from the bytes.

## Decision

We will treat the bytes a source served as the thing being stored, and extract text only
when indexing needs it.

- Loaders yield `SourceDocument(content: bytes, metadata: dict)` instead of a LangChain
  `Document`. A loader that fetches a PDF hands on the PDF.
- `document_source_service` writes those bytes verbatim and lets `File.create` sniff the
  content type from them, ignoring any type the source claimed. An update re-sniffs, since
  the same identifier may now serve a different format.
- `Document.from_file` already picks a reader from `File.content_type`, so a stored PDF is
  parsed as a PDF at index time. Extraction moves into the indexing worker.
- A file that yields no text at index time is recorded FAILED, not COMPLETED. Both index
  managers enforce this; the remote one only does so when the local read *succeeded* and
  returned nothing, deferring to the provider whenever our readers cannot parse the format
  at all.

Where a source has no file to serve — Confluence pages, GitHub's decoded file contents — the
loader encodes the text it was given. The invariant is "the rawest representation the source
offers", not "always a binary blob".

## Consequences

- A synced file downloads as the document it claims to be, and its content type describes
  what is stored.
- Attachments are now persisted in full, up to the 50 MB per-download cap that already
  bounded the fetch (`MAX_RESPONSE_BYTES`, matching `MAX_FILE_SIZE_MB`). Storage for a
  PDF-heavy feed grows from extracted text to whole documents.
- A corrupt or scanned PDF is no longer refused during sync in milliseconds; it is stored,
  and its parse cost lands on the indexing worker instead. Re-indexing re-parses.
- The remote index path now reads each file locally before upload to check it yields text.
  That is work the provider would otherwise do, accepted because the provider reports
  nothing for a file it indexed as empty.
- Rows synced before this decision are unaffected: they still hold extracted text under the
  source's filename and are only rewritten when their `sha`/`date` changes. Repairing them
  needs a forced re-sync, which is not part of this decision.
- `_update_file` must now clear `File.external_id`, because an update can change the format
  outright and the remote index skips re-uploading a file whose id still resolves.

## Alternatives considered

- **Trust the feed's `file_type` and store it as the content type**: rejected — it is an
  unverified third-party claim, and sniffing the bytes we hold costs nothing.
- **Keep extracting at sync time, but store the raw bytes alongside the text**: rejected —
  two representations of one document to keep in step, and the reader already runs at index
  time for every non-synced file.
- **Rename the stored file to match its extracted-text content (`report.pdf` → `report.txt`)**:
  rejected — the citation metadata and the source's own identifier both reference the
  original name, and the text is still a lossy derivative a user cannot re-derive from.
- **Poll the remote provider for per-file index status instead of reading files locally**:
  rejected for now — it is the more honest signal and would also catch provider-side failures
  this decision does not, but it means blocking a Celery task on OpenAI's batch processing.
  Worth its own ADR.
