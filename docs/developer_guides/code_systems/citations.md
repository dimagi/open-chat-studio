# Citations

The platform has a built-in mechanism for citing sources used by the LLM, particularly when retrieving information from indexed documents. This process involves generation, parsing, and final rendering of citations.

This assumes the LLM has retrieved content from a document collection — see [Index Manager Classes](index_managers.md) for how that retrieval works.

## 1. Citation Generation

When an agent uses a tool like `SearchIndexTool` to query a document collection, it gains access to the content of relevant files. The LLM is instructed to cite its sources by embedding a special tag in its response whenever it uses information from a file.

The citation format is `<CIT file-id />`, where `file-id` is the unique identifier of the cited `File` model instance. This format is defined by the `OCS_CITATION_PATTERN` constant found in `apps.chat.agent.tools`.

## 2. Building the Reference Section

After the LLM generates a response containing these citation tags, the system processes the message to create a human-readable reference section. This is handled by `apps.service_providers.llm_service.utils.populate_reference_section_from_citations`.

This method performs three main actions:
1. It scans the AI message for all instances of the `<CIT file-id />` pattern.
2. Each tag is replaced with a footnote-style reference (e.g., `[^1]`, `[^2]`). It keeps track of cited files to reuse reference numbers for multiple citations of the same file.
3. It appends a markdown-formatted reference list to the end of the message. Each entry includes the reference number, the original file name, and a download link for that file.

This processing step occurs within the `invoke` method of the `LLMChat` runnable (`apps/service_providers/llm_service/runnables.py`) before the final message is saved and sent to the user.

Example transformation:

**Input from LLM:**
```
The sky is blue <CIT 123 />. The grass is green <CIT 456 />.
```

**Output after processing:**
```markdown
The sky is blue [^1]. The grass is green [^2].

[^1]: [document_a.pdf](https://example.com/download/123)
[^2]: [source_b.txt](https://example.com/download/456)
```

## 3. Final Rendering in Channels

The markdown-formatted message, now including footnote-style references, is passed to the channel layer for final display to the user. In `apps/chat/channels.py`, a function named `_format_reference_section` is responsible for parsing this markdown and rendering it appropriately for the specific channel (e.g., converting it to HTML for the web interface). This ensures that users see a clean, clickable list of citations in the final UI.
