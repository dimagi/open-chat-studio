# ADR-0052: Manifest-first change detection

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-08-05</p>

## Context

A sync decided whether a file had changed by comparing a version token in the fetched document's metadata against the stored copy — a comparison only possible *after* downloading the file in full. A resumed sync therefore re-downloaded every file to conclude it had nothing to do, even though for GitHub the version token is already present in the git-tree listing that enumerates the repository.

The set of identifiers seen during a run was also built inside the fetch loop, so a run that stopped early treated every file it had not yet reached as removed from the source.

## Decision

We will let a loader enumerate its source without fetching content, and compute the diff from that listing.

- A `ManifestEntry` carries an identifier, an opaque version token, and a loader-private handle. `list_manifest()` returns `None` by default, so a loader that implements nothing keeps the streaming path.
- The stored version token is read from `File.metadata` under a loader-declared `version_metadata_key`. That field is already populated for every file ever synced, so there is no migration, no backfill, and the diff works retroactively.
- One `is_current()` predicate defines "does not need fetching" for both paths. A blank token on either side is never current.
- A manifest-capable loader derives `load_documents()` from its own manifest, so the two cannot drift on identifier construction.
- The diff is a pure function of identifiers and version tokens. Both the listing and the stored side are streamed, so memory is bounded by the source's identifiers plus the work actually outstanding.
- GitHub and JSON collection adopt it. Confluence does not.

## Consequences

- A sync killed at file 900 of 1000 resumes with one listing call and 100 fetches rather than 1,000.
- The seen-set is complete before anything destructive happens, so a partial run never deletes live files and a listing error deletes nothing.
- A failed fetch becomes a per-file failure rather than an absence, so a transient download error no longer deletes the file — which it previously did.
- The version token's provenance is loader metadata we do not own; a key renamed upstream would make every file look new. An equivalence test guards this, not the type system.
- Confluence gets no fetch-skipping: a resume re-downloads the source, though it still skips every content write and embedding for work already done.
- A genuine first sync costs exactly what it did before; there is nothing to skip when everything is new.
- Directory nodes must be filtered out of GitHub's listing, having previously been swallowed only because fetching one returned empty content.

## Alternatives considered

- **A dedicated version column on `CollectionFile`** — rejected: `File.metadata` already holds the token for every file ever synced, so a column costs a migration and a backfill to store a value we can already read.
- **A manifest for every loader, including Confluence** — rejected: LangChain's Confluence loader returns page bodies inline with the listing, so a body-less listing means writing our own API calls, and it trades one batched paginated call for a listing plus one request per changed page.
- **A per-loader `should_update_document` override, as before** — rejected: three near-identical implementations of one comparison, and two comparison paths that can silently disagree.
