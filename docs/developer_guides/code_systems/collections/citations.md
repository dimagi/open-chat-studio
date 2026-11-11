# Citations

When a chatbot answers from OCS collections, it can cite the files it used. The user sees numbered references in the answer, and a list at the end with each file's name and a download link. Citations are produced by pipeline LLM nodes with the `generate_citations` setting on. See the [user guide for citations](https://docs.openchatstudio.com/concepts/collections/indexed_collections/citations/) for the user's view, and [Index Manager Classes](index_managers.md) for how the retrieval works.

## Example

**LLM response (before the build step):**
```text
The sky is blue <CIT 123 />. The grass is green <CIT 456 />.
```

**Message after the build step:**
```markdown
The sky is blue [^1]. The grass is green [^2].

[^1]: [document_a.pdf](https://example.com/download/123)
[^2]: [source_b.txt](https://example.com/download/456)
```

Non-web channels show `[1]` instead of `[^1]` (see [section 4](#4-final-rendering-in-channels)).

## The citation chain

Citations are processed in four steps, from the LLM writing its response to the user seeing the answer:

1. **Generate** ([section 1](#1-citation-generation)): The LLM writes a citation wherever it uses information from a retrieved file.
2. **Collect** ([section 2](#2-collecting-cited-files)): The output parser reads the citations and resolves them to cited files.
3. **Build** ([section 3](#3-building-the-reference-section)): Each citation is replaced with a reference and the reference section is appended to the message.
4. **Render** ([section 4](#4-final-rendering-in-channels)): The channel layer adapts the references to what the channel supports, such as plain `[1]` markers or native file attachments.

References come only from `<CIT file-id />` citations, so only local indexes produce them. Files cited through a remote index are covered in [section 1](#1-citation-generation) and [section 2](#2-collecting-cited-files).

## Terms

- **Citation**: the `<CIT file-id />` text the LLM writes in its response, where `file-id` is the ID of a `File`.
- **Cited files**: the set of `File` records the response cites (`cited_files`).
- **Reference**: the numbered footnote (`[^1]`) that replaces a citation in the message.
- **Reference section**: the list appended to the end of the message, with one line per reference. Each line has the reference number, the file's citation text and a download link. The citation text is the file name unless the file's metadata overrides it.

## 1. Citation Generation

A file is cited in one of two ways, depending on where the collection's index lives:

1. **Local index: the LLM writes the citations.** The search tool returns the matching chunks, each labelled with the `file_id` and `filename` of its file. If `generate_citations` is on, the tool also tells the LLM to write a citation after each claim that uses that content, putting that chunk's `file_id` in the `<CIT file-id />` citation. These citations become references.
2. **Remote index (OpenAI vector store): the provider reports the citations.** OpenAI's built-in file search has no citation instruction, so the LLM writes no citations. Cited files come only from [provider annotations](#2-collecting-cited-files). They are attached to the chat but get no reference.

## 2. Collecting Cited Files

After the LLM responds, the node passes the response to an output parser, which returns the text plus `cited_files`. Anthropic has its own parser; every other provider uses the default parser.

| Scenario | Parser | `cited_files` come from | Gets references? |
|---|---|---|---|
| 1. Local index, Anthropic | Anthropic parser | `<CIT file-id />` citations in the text | Yes |
| 2. Local index, other provider | Default parser | `<CIT file-id />` citations in the text | Yes |
| 3. Remote index, OpenAI | Default parser | **Provider annotations** | No |

**Why Anthropic has its own parser.** [Anthropic's web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool#citations) returns its sources as `citations` on the response's text blocks. The Anthropic parser adds them to the text as `[title](url)` links. They are links, not files, so they never appear in `cited_files` or the reference section.

**Provider annotations** are entries in the `annotations` list of a LangChain content block. For OpenAI, entries of type `citation` carry the file's external ID, which the default parser matches to files by `File.external_id`. Currently only OpenAI supplies them.

Rules for all scenarios:

- When the node's `generate_citations` setting is off, `cited_files` is left empty. The node then removes any citations from the message instead of building references.
- A file in `cited_files` is attached to the chat. It gets a reference only if its citation is in the text, because [section 3](#3-building-the-reference-section) only numbers files that have one. This is why scenario 3 has no references.

## 3. Building the Reference Section

After the LLM generates a response containing these citation tags, the system processes the message to create a human-readable reference section. This is handled by `apps.service_providers.llm_service.utils.populate_reference_section_from_citations`.

This method performs three main actions:
1. It scans the AI message for all instances of the `<CIT file-id />` pattern. A tag whose ID is not in `cited_files` is removed from the message.
2. Each tag is replaced with a footnote-style reference (e.g., `[^1]`, `[^2]`). It keeps track of cited files to reuse reference numbers for multiple citations of the same file.
3. It appends a markdown-formatted reference list to the end of the message. Each entry includes the reference number, the original file name, and a download link for that file.

This processing step occurs in `_process_agent_output()` in `apps/pipelines/nodes/llm_node.py`, before the final message is saved and sent to the user.

## 4. Final Rendering in Channels

The markdown-formatted message, now including footnote-style references, is passed to the channel layer for final display to the user.

In `apps/channels/stages/core.py`, `ResponseFormattingStage._format_reference_section` adapts this markdown for the specific channel's capabilities — it rewrites `[^1]`-style footnotes to `[1]` for non-web channels, and for each cited file shows either just the filename (if the channel can send the file natively) or the filename with a download link (if it can't). The message stays in markdown; any HTML rendering happens elsewhere, in the web interface's own message rendering.
