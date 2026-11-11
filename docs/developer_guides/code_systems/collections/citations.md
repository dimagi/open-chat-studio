# Citations

The platform has a built-in mechanism for citing sources used by the LLM, particularly when retrieving information from indexed documents. This process involves generation, parsing, and final rendering of citations.

This page covers the `<CIT file-id />` mechanism used by **pipeline nodes**, which is the only source of references. Provider-native citations are described in [section 2](#2-collecting-cited-files).

## Terms

- **Citation**: the `<CIT file-id />` text the LLM writes in its response, where `file-id` is the ID of a `File`.
- **Cited files**: the `File` records the response cites (`cited_files`).
- **Reference**: a numbered footnote (`[^1]`) that replaces a citation in the response message, plus its matching line in the reference section.
- **Reference section**: the list of references appended to the end of the message. Each line has the reference number, the file's citation text and a download link. The citation text is the file name unless the file's metadata overrides it.
- **Provider annotation**: an entry in the `annotations` list of a LangChain content block. For OpenAI, entries of type `citation` carry the remote file ID of a cited file, outside the response text. The default parser reads them.
- **Native citation**: an entry in the `citations` list of an Anthropic text block. The Anthropic parser appends each entry that has a title and URL to the text as a link.

## 1. Citation Generation

The LLM is instructed to write a citation after each claim that uses retrieved content. The citation format is defined once, in the chat agent constants, and shared by every step below.

## 2. Collecting Cited Files

All providers fill `cited_files` from the citations in the response text. Some also contribute extra sources:

- **Default parser** (all providers except Anthropic): also reads provider annotations, matched to files by `File.external_id`. Currently only OpenAI supplies them.
- **Anthropic parser**: reads the citations only. It also appends Anthropic's native citations to the text as plain `[title](url)` links. These are links, not files, so they never appear in `cited_files` or the reference section.

Common rules for both parsers:

- IDs are resolved in a single query scoped to the session's team. An ID that matches no file in the team is dropped.
- When the node's `generate_citations` setting is off, `cited_files` is left empty. The node then removes any citations from the message instead of building references.
- A file cited only through a provider annotation is in `cited_files` and so is attached to the chat, but it gets no reference, because [section 3](#3-building-the-reference-section) only numbers files that have a citation in the text.

## 3. Building the Reference Section

After the LLM generates a response containing these citation tags, the system processes the message to create a human-readable reference section. This is handled by `apps.service_providers.llm_service.utils.populate_reference_section_from_citations`.

This method performs three main actions:
1. It scans the AI message for all instances of the `<CIT file-id />` pattern. A tag whose ID is not in `cited_files` is removed from the message.
2. Each tag is replaced with a footnote-style reference (e.g., `[^1]`, `[^2]`). It keeps track of cited files to reuse reference numbers for multiple citations of the same file.
3. It appends a markdown-formatted reference list to the end of the message. Each entry includes the reference number, the original file name, and a download link for that file.

This processing step occurs in `_process_agent_output()` in `apps/pipelines/nodes/llm_node.py`, before the final message is saved and sent to the user.

Example transformation:

**Input from LLM:**
```text
The sky is blue <CIT 123 />. The grass is green <CIT 456 />.
```

**Output after processing:**
```markdown
The sky is blue [^1]. The grass is green [^2].

[^1]: [document_a.pdf](https://example.com/download/123)
[^2]: [source_b.txt](https://example.com/download/456)
```

## 4. Final Rendering in Channels

The markdown-formatted message, now including footnote-style references, is passed to the channel layer for final display to the user.

In `apps/channels/stages/core.py`, `ResponseFormattingStage._format_reference_section` adapts this markdown for the specific channel's capabilities — it rewrites `[^1]`-style footnotes to `[1]` for non-web channels, and for each cited file shows either just the filename (if the channel can send the file natively) or the filename with a download link (if it can't). The message stays in markdown; any HTML rendering happens elsewhere, in the web interface's own message rendering.
