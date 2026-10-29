# Citations

The platform has a built-in mechanism for citing sources used by the LLM, particularly when retrieving information from indexed documents. This process involves generation, parsing, and final rendering of citations.

This page covers the `<CIT file-id />` tag mechanism used by **pipeline nodes** (`apps/pipelines/nodes/llm_node.py`). It is the only mechanism that produces a reference section. Two other sources interact with it:
- Provider citation annotations (such as OpenAI file citations) add files to `cited_files`, as described in [Collecting Cited Files](#2-collecting-cited-files).
- Anthropic's native web citations are appended to the text as plain `[title](url)` links by `parse_output_for_anthropic`. They bypass the reference section.

This assumes the LLM has retrieved content from a document collection — see [Index Manager Classes](index_managers.md) for how that retrieval works.

## 1. Citation Generation

When an agent uses a tool like `SearchIndexTool` to query a document collection, it gains access to the content of relevant files. The LLM is instructed to cite its sources by embedding a special tag in its response whenever it uses information from a file.

The citation format is `<CIT file-id />`, where `file-id` is the unique identifier of the cited `File` model instance. This format is defined by the `OCS_CITATION_PATTERN` constant found in `apps.chat.agent.constants`.

## 2. Collecting Cited Files

Before the reference section is built, the LLM output is parsed into an `LlmChatResponse` whose `cited_files` field holds the `File` records the response cites. `_process_agent_output()` gets the parser from the node's LLM service (`get_output_parser()`) and passes `include_citations=node.generate_citations`.

`LlmService._default_parser`, used by every provider except Anthropic, fills `cited_files` from two sources:
- The IDs found in `<CIT n />` tags in the response text (`extract_file_ids_from_ocs_citations`).
- The provider's own citation annotations (`get_cited_file_ids`), matched to files by `File.external_id`. Only `OpenAILlmService` returns annotation IDs; the base implementation returns none.

Both sets of IDs are resolved with a single `File` query scoped to the session's team. An ID that matches no file in the team is dropped. When `include_citations` is false, `cited_files` is left empty.

The Anthropic parser (`parse_output_for_anthropic`) overrides `get_output_parser()`. It fills `cited_files` from the `<CIT n />` tags only (`get_cited_files_from_text`), looked up once on the assembled response text with the same team scoping and `include_citations` behaviour. It also appends Anthropic's native web citations to the text as `[title](url)` links, which do not affect `cited_files`.

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
