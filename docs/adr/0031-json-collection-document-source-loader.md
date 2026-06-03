# ADR-0031: JSON Collection document source as a hard-coded loader in the existing framework

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-03</p>

## Context

Some knowledge sources expose their content as a top-level JSON list of items, each with metadata and attachment links (e.g. ESPEN-style document libraries). No loader existed for this pattern; manual upload neither scales nor captures the structured per-item metadata (type, languages, regions, diseases, etc.) that downstream RAG can use for citation and filtering.

The collections framework already defines an extension surface: `BaseDocumentLoader` subclasses registered per `SourceType`, driven by `DocumentSourceManager`, with per-source-type config carried in a pydantic `DocumentSourceConfig`. The question was how much new machinery a JSON-feed loader needs.

## Decision

We will add a `JSON_COLLECTION` source type served by a new `JSONCollectionLoader` that plugs into the existing framework, with the indexed-collections JSON shape hard-coded for v1.

- New `SourceType.JSON_COLLECTION = "json_collection"`; the loader is registered in the `source_type → loader` map; config is carried in `DocumentSourceConfig.json_collection` (a `JSONCollectionSourceConfig` holding `json_url` and `request_timeout`).
- No schema change to `DocumentSource` or `CollectionFile`; extending the `SourceType` choices emits only a metadata-only, backwards-compatible `AlterField` migration.
- The field mapping for the indexed-collections shape is hard-coded; configurable mapping is deferred.
- Exposure is gated by the `flag_json_collection_loader` waffle flag: it hides the source-type picker tile and makes the creation view return 404 (including on direct POST). The flag does NOT gate the loader, its registry entry, or scheduled syncs of already-created sources.

## Consequences

- `DocumentSourceManager` was unchanged; the loader conforms to the existing interface.
- v1 ingests only the one hard-coded shape; a new feed format needs code, not config.
- Disabling the flag for a team stops new JSON sources but leaves existing sources syncing — the flag is a creation gate, not a kill switch.
- A per-source `request_timeout` (5–300s, default 30) bounds the many third-party calls a single sync makes.

## Alternatives considered

- **Configurable field mapping in v1** → deferred; one hard-coded shape covers the initial use case and avoids designing a mapping DSL prematurely.
- **Schema columns to store per-source mapping/config** → rejected; the existing pydantic `DocumentSourceConfig` already carries per-type config with no DB change.
- **Gating the loader and scheduled syncs behind the flag too** → rejected; disabling the flag must not break an existing team's ongoing syncs.
