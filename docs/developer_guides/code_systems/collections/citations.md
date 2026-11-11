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

Messaging channels such as Telegram and WhatsApp show `[1]` instead of `[^1]` (see [section 4](#4-final-rendering-in-channels)).

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

The pipeline LLM node builds the reference section after the output parser, when `generate_citations` is on. The result is the message that is saved and sent on.

- Citations are numbered in the order they first appear in the message.
- Repeat citations of the same file reuse its number.
- A citation whose file is not in `cited_files` is removed. This drops IDs the LLM made up.
- The reference section is appended after a blank line, one line per reference in number order: `[^1]: [citation text](download link)`. The download link is the file's own URL if its metadata sets one, otherwise the platform's download URL for the session.

## 4. Final Rendering in Channels

How the references reach the user depends on the channel.

- **Web and API channels** return the saved markdown unchanged. The web interface renders the `[^1]` footnotes itself.
- **Messaging channels** (those that send the reply themselves) rewrite the message before sending:
  - `[^1]` becomes `[1]` throughout, because plain-text apps do not support footnotes.
  - Each reference line shows the citation text, followed by the download link only if the channel cannot send the file as an attachment.
  - Voice replies skip the reference formatting. The URLs are taken out of the spoken text and sent as a separate text message.
