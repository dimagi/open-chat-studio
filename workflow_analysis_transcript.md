# Chatbot Transcript Analysis Pipeline: Workflow Analysis

## Workflow Overview

**Trigger node name:** "Chatbot Analysis Trigger"
**Form title:** "Chatbot Transcript Analysis Pipeline: Upload"
**Purpose:** End-to-end automated analysis of MBW (Mother and Baby Wellness) chatbot conversation transcripts. The pipeline ingests raw transcript data alongside a QnA knowledge bank, performs two independent LLM/code-based analyses — **Coverage** (did the user's message map to a known Q&A entry?) and **Accuracy** (did the bot's response correctly answer the question?) — and writes all results to a new Google Sheets workbook with six tabs. It also generates engagement and session metrics from the same transcript file. A confirmation email is sent to the submitter upon completion.

---

## Input

The trigger is an n8n web form that collects:

| Field | Type | Details |
|---|---|---|
| Question Bank | File upload | `.xlsx`, `.xls`, or `.csv`; the curated QnA knowledge base |
| Transcript | File upload | `.xlsx`, `.xls`, or `.csv`; raw chatbot conversation export |
| Email ID | Email | Recipient for the output notification |
| Project | Dropdown | Fixed to "MBW Chatbot Transcript Analysis" |
| Bot | Dropdown | "FLW" (Front Line Worker) or "Mother" — controls which bot's rules apply |

---

## Node-by-Node Description

### 1. Chatbot Analysis Trigger
- **Type:** `n8n-nodes-base.formTrigger`
- **ID:** `57c0ce86-4611-4ead-886b-7ac2991114d3`
- **Purpose:** Presents the web form described above. On submission, fires three parallel branches: transcript extraction (for Q&A pairing), QnA bank extraction, spreadsheet creation, and bot-type forwarding.

---

### 2. Extract from File
- **Type:** `n8n-nodes-base.extractFromFile`
- **ID:** `128f6476-02d1-4d83-aa3b-2037f30724bc`
- **Input:** The binary `Transcript` file attachment from the form trigger.
- **Purpose:** Parses the uploaded transcript file (CSV/Excel) into row-level JSON objects. Each row represents one message with columns including `Session ID`, `Message Date`, `Message Type` (human/ai), `Message Content`, `Participant Public ID`, etc.
- **Output:** One item per message row, fanning out to: QnA Pairing, Weekly Activity Summary Generator, Weekly Session Summary Generator, and User Engagement Summary Generator.

---

### 3. Edit Fields3
- **Type:** `n8n-nodes-base.set`
- **ID:** `080a583d-cea8-4dfb-b720-e05c31286517`
- **Purpose:** Extracts and forwards the `Bot` field (FLW or Mother) from the form trigger payload. This value is carried forward for the Coverage Analysis node to apply bot-specific matching rules.
- **Output:** Single field `Bot`.

---

### 4. Extract qna bank o/ps
- **Type:** `n8n-nodes-base.extractFromFile` (xlsx operation)
- **ID:** `92c7154d-adf2-4912-990e-b93fe7530834`
- **Input:** The binary `Question_Bank` file attachment.
- **Purpose:** Parses the QnA bank Excel file into rows. Each row is expected to have at minimum a `question` (or `Question`) field and a `qna_id` (or `id`) field.
- **Output:** One item per QnA row; forwarded to "Wait" (Merge node, input 1) to be held until the transcript QnA pairs are ready.

---

### 5. QnA Pairing
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `0805df80-f41e-4f86-9b17-6c7546b49537`
- **Input:** All transcript rows from "Extract from File".
- **Purpose:** Groups transcript rows into sequential human→AI turn pairs. The logic:
  1. Sorts all rows by `Session ID` then `Message Date`.
  2. Iterates through rows tracking the current session.
  3. When `Message Type` is "human", stores the human message (stitches consecutive human turns together with newlines).
  4. When `Message Type` is "ai" and a pending human message exists, emits a pair object and resets.
  5. Session resets clear the pending human state.
- **Output fields per pair:** `pair_index`, `session_id`, `human_text`, `ai_text`, `human_timestamp`, `ai_timestamp`.

---

### 6. Convert to File
- **Type:** `n8n-nodes-base.convertToFile`
- **ID:** `7db9147c-e426-4239-8bc0-86c1e09fdc17`
- **Purpose:** Serialises the QnA pairs from in-memory JSON back to a CSV binary named `KMC_UAT_QnA_Pairs.csv`. This round-trip is necessary so the next node can re-read it.

---

### 7. Extract pair o/ps
- **Type:** `n8n-nodes-base.extractFromFile`
- **ID:** `e7d53683-d652-4d9a-bfff-cbeb331f5eb5`
- **Purpose:** Re-parses the CSV binary back to JSON row items, ensuring proper column typing. Output fans into the translation branch (input 0) and the merge node Merge7 (input 0) simultaneously.

---

### 8. Translate a language
- **Type:** `n8n-nodes-base.googleTranslate`
- **ID:** `ce1209d1-fce9-493e-bcf7-1398a30e248f`
- **Credentials:** Google Translate OAuth2 (account: "Google Translate account")
- **Input:** For each QnA pair: `{{ $json["﻿pair_index"] }} - {{ $json.human_text }}` — the pair index prepended to the human message, joined with " - ".
- **Target language:** English (`en`).
- **Purpose:** Translates user messages that may be in local languages (Hausa, Pidgin, Yoruba, etc.) into English so the subsequent coverage analysis can perform keyword matching against the English-language QnA bank.
- **Output:** `translatedText`, `detectedSourceLanguage`.

---

### 9. Code in JavaScript1
- **Type:** `n8n-nodes-base.code`
- **ID:** `3a76d37e-7c98-4b02-911a-f6671d3afe02`
- **Purpose:** Post-processes the Google Translate output. Key steps:
  1. Decodes HTML entities that Google Translate may introduce (`&amp;`, `&#39;`, numeric entities, etc.).
  2. Strips BOM and zero-width Unicode characters.
  3. Normalizes whitespace.
  4. Splits on the first occurrence of " - " to separate `pair_index` from `human_text_en`. Only splits if the left side is a pure integer.
- **Output fields added:** `pair_index`, `human_text_en`, `human_lang` (detected source language), `raw_translatedText` (kept for debugging).

---

### 10. Merge7
- **Type:** `n8n-nodes-base.merge` (combine, enrichInput1, merge by `﻿pair_index` = `pair_index`)
- **ID:** `de6992f2-11c1-45a3-94e1-476f1f3b00a0`
- **Purpose:** Left-joins the original QnA pair data (from "Extract pair o/ps", input 0) with the translated English text (from "Code in JavaScript1", input 1), keyed on `pair_index`. This enriches each pair record with the `human_text_en` and `human_lang` fields without losing any original fields.

---

### 11. Edit Fields2
- **Type:** `n8n-nodes-base.set`
- **ID:** `e0e2cc6d-5990-4e9a-bd00-486d19ca8bc6`
- **Purpose:** Selects and re-emits a clean subset of fields from the merged data: `﻿pair_index`, `session_id`, `human_text_en`, `ai_text`, `human_timestamp`, `ai_timestamp`.

---

### 12. Merge6
- **Type:** `n8n-nodes-base.merge` (default/append mode)
- **ID:** `03d3de93-6a39-4ac2-a5c4-24bc7d195d55`
- **Purpose:** Synchronisation gate. Waits for both the QnA pair data (from "Edit Fields2", input 0) and the Bot metadata (from "Edit Fields3", input 1) to be available before passing to "Wait".

---

### 13. Wait
- **Type:** `n8n-nodes-base.merge` (chooseBranch)
- **ID:** `9e8fe3db-c4ae-42ed-8279-29c9a4f9e799`
- **Purpose:** Synchronisation gate. Waits for both the enriched QnA pairs (from "Merge6", input 0) and the extracted QnA bank rows (from "Extract qna bank o/ps", input 1) before triggering the coverage analysis.

---

### 14. Coverage Analysis: JS
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `92018c42-048a-4442-a617-613661fa0500`
- **Purpose:** The primary **Coverage Analysis** engine. For each QnA pair, classifies the user's message against the QnA bank using rule-based logic and Jaccard similarity scoring.

**Algorithm detail:**

1. **Loads the QnA bank** from the `Extract qna bank o/ps` node via `$items()`.
2. **Text normalisation:** Lowercases, strips non-alphanumeric characters, collapses whitespace.
3. **FLW-specific normalisation (when `Bot === "FLW"`):** Strips teaching-wrapper prefixes such as "If a mother asks me...", "How do I explain...", "What should I tell a mother..." before matching, so FLW phrasing doesn't interfere with question matching.
4. **Rule-based pre-classification (in order):**
   - Empty input → `Non-question / clarification`
   - Greetings/gratitude/social (regex: hi, hello, thanks, amen, amin, 🙏, 👍, etc.) → `Non-question / niceties_greetings`
   - Language choice (hausa, english, pidgin, yoruba, igbo, swahili) → `Non-question / language_selection`
   - Acknowledgements (ok, yes, no, i understand, etc.) → `Non-question / clarification`
5. **Question intent check:** If the text contains "?" or starts with question words (what, why, how, when, where, who, can, should, is, are, do, does, did, will):
   - If no health/KMC keywords detected → `Unmatched question / out_of_scope`
   - Otherwise, compute Jaccard similarity against every QnA bank entry (using word tokens of length > 2). Threshold: **0.20** (20%).
   - If best score >= 0.20 → `Matched question` with `matched_qna_id` and `match_score`
   - Else → `Unmatched question / in_scope`
6. **Health statement** (not a question but contains health-related keywords like baby, child, pregnant, etc.) → `Non-question / health_statement`
7. **Default fallback** → `Unmatched question / out_of_scope`

**Output fields per item:** All original fields plus `coverage_main`, `coverage_subtype`, `coverage_reason`, `matched_qna_id`, `coverage_text_used`, `coverage_text_source`, `match_score` (on matches).

**Coverage taxonomy:**
- `coverage_main`: `Matched question` | `Unmatched question` | `Non-question`
- `coverage_subtype`: `in_scope` | `out_of_scope` | `niceties_greetings` | `language_selection` | `clarification` | `health_statement` | _(null for matched questions)_

---

### 15. Coverage:Python O/P (disabled in flow but present)
- **Type:** `n8n-nodes-base.convertToFile`
- **ID:** `73d6f362-fc74-4ddc-8925-794c8ac4576e`
- **Purpose:** Saves the raw coverage output as `JS_Coverage_OP.csv`. Present for debugging/validation comparison.

---

### 16. Set Columns (disabled)
- **Type:** `n8n-nodes-base.set`
- **ID:** `b4054904-69b6-49d5-bf39-f9629a87183c`
- **Status:** Disabled — not part of the live execution path.

---

### 17. Filter columns - Coverage - Accuracy (disabled path)
- **Type:** `n8n-nodes-base.set`
- **ID:** `644de08c-d509-4806-92cd-1cbad1b77a94`
- **Status:** Part of a disabled branch — not active.

---

### 18. OpenAI: Coverage check (disabled)
- **Type:** `@n8n/n8n-nodes-langchain.openAi`
- **ID:** `a78abfd7-923e-44f1-bb1f-8286df01c03b`
- **Status:** Disabled — this was a prior LLM-based coverage classification approach, now superseded by the JavaScript Jaccard-based engine.
- **Model:** `gpt-4.1-nano`
- **Note:** The prompt is preserved below for reference.

**System instructions (disabled):**
```
You are evaluating user messages for a Kangaroo Mother Care (KMC) chatbot.
Your task is STRICT CLASSIFICATION ONLY.
You must:
- Copy the provided EVAL_ID exactly into the output field "eval_id"
- Classify the USER MESSAGE using the provided taxonomy
- Decide whether it matches a known QnA entry ONLY when QnA context is explicitly provided
You must NOT:
- Answer the user
- Provide medical or health advice
- Rewrite or expand the content
- Add explanations outside the rationale field
- Invent, alter, or omit the eval_id
STRICT INTENT RULE:
- If the USER MESSAGE is phrased as a question or is clearly seeking information, it MUST NOT be classified as non_question.
SCOPE RULE:
- If it is an information-seeking question but NOT directly about KMC practices, classify as unmatched_question + out_of_scope.
MATCHED QUESTION GUARDRAIL:
- Do NOT classify a message as matched_question unless BOTH a QnA Question AND an Expected Answer are explicitly provided in the prompt.
Rules:
- You must follow the taxonomy exactly
- Invalid label combinations are NOT allowed
- Use ONLY the information provided in the prompt
- Return ONLY valid JSON
- Do NOT wrap output in markdown
- Do NOT add commentary or extra text
```

**User message template (disabled):**
```
You are performing STRICT CLASSIFICATION for a Kangaroo Mother Care (KMC) chatbot.

––––––––––––––––––––
EVAL ID (copy exactly):
{{ $json['﻿pair_index'] }}

USER MESSAGE:
{{ $json.human_text }}

REFERENCE CONTEXT (previous bot message, if any):
{{ $json.prev_ai_text || "None" }}

QnA CONTEXT (only if BOTH provided):
Question: {{ $json.qna_question || "None" }}
Expected Answer: {{ $json.expected_answer || "None" }}

––––––––––––––––––––
COVERAGE TAXONOMY

coverage_main (choose ONE):
- non_question
- matched_question
- unmatched_question

coverage_subtype rules:
- non_question → clarification | niceties | refusal_apology
- unmatched_question → in_scope | out_of_scope
- matched_question → MUST be null

––––––––––––––––––––
RULES (MANDATORY)

- If USER MESSAGE seeks information → NOT non_question
- Non-KMC health topics → unmatched_question + out_of_scope
- matched_question ONLY if BOTH QnA Question and Expected Answer exist AND match
- suggested_qna_topic ONLY if unmatched_question + in_scope

––––––––––––––––––––
OUTPUT (JSON ONLY)

{
  "eval_id": "<copy exactly>",
  "human_text": "<copy exactly>",
  "ai_text": "<copy exactly>",
  "coverage_main": "<string>",
  "coverage_subtype": "<string or null>",
  "rationale": "<one short sentence>",
  "suggested_qna_topic": "<string or null>"
}

Return ONLY the JSON. No extra text.
```

---

### 19. Clean OpenAI O/P (disabled)
- **Type:** `n8n-nodes-base.code`
- **ID:** `1c4c08bd-e9c6-4f7b-9d78-718a9d3e5ed5`
- **Status:** Disabled — only used when the OpenAI coverage check is active.

---

### 20. Merge: All Coverage O/Ps (disabled)
- **Type:** `n8n-nodes-base.merge`
- **ID:** `7e29144e-7be2-46a6-9d2f-014fcfcf88b9`
- **Status:** Disabled.

---

### 21. Coverage Analysis Output [Python vs LLM] (disabled)
- **Type:** `n8n-nodes-base.convertToFile`
- **ID:** `bbc2bb60-6d36-4da3-93e8-c0adab61b0a7`
- **Status:** Disabled debug artifact.

---

### 22. If (Conditional Branch for Accuracy Analysis)
- **Type:** `n8n-nodes-base.if`
- **ID:** `474c9b5c-e96f-4a26-9630-fae54acefe4f`
- **Condition:** `coverage_main` equals `"Matched question"` (string, case-sensitive)
- **True branch (output 0):** Items where the user's message matched a QnA entry — these proceed to the Accuracy analysis.
- **False branch (output 1):** Non-questions and unmatched questions — these bypass accuracy analysis but are still collected for the overall summary.
- **Purpose:** Accuracy analysis is only meaningful for messages that matched a known Q&A entry; there's no expected answer to evaluate otherwise.

---

### 23. Filter: 20 (disabled)
- **Type:** `n8n-nodes-base.filter`
- **ID:** `f0ca3aeb-bdf1-4e12-bc76-bd33870c51be`
- **Status:** Disabled — was a development limiter to test with only the first 20 pairs.

---

### 24. Message a model (Accuracy Analysis LLM)
- **Type:** `@n8n/n8n-nodes-langchain.openAi`
- **ID:** `e95ee3ed-e6de-4afa-a638-b383f28851ac`
- **Credentials:** OpenAI account ("OpenAi account")
- **Model:** `gpt-4.1-nano` (implied from the credential and node naming convention; the modelId is not separately visible in this node's parameters but the pattern matches)
- **Temperature:** 0, topP: 1, maxTokens: 500
- **Purpose:** LLM-based **Accuracy Classification** for each matched Q&A pair. Evaluates whether the bot's response correctly, safely, and appropriately answered the user's message.

**System prompt (full text):**
```
You are performing STRICT ACCURACY CLASSIFICATION for a Mother and Baby Wellness (MBW) chatbot used by BOTH Front Line Workers (FLWs) and Mothers.

Your task is to evaluate whether the BOT RESPONSE appropriately answers the USER MESSAGE.

You must output ONLY valid JSON.
Do NOT include markdown, explanations, or extra text.

You must:

Copy all provided input fields EXACTLY into the output

Assign ONE accuracy_main value

Assign accuracy_subtype ONLY IF accuracy_main = non_answer

Base your decision ONLY on the USER MESSAGE and BOT RESPONSE

Be strict for newborn and preterm danger signs

You must NOT:

Answer the user

Provide medical or health advice

Rewrite, correct, or expand the content

Invent missing information

Alter, omit, or leave blank any provided identifiers

ACCURACY TAXONOMY

accuracy_main (choose ONE):

accurate

inaccurate

non_answer

accuracy_subtype (ONLY when accuracy_main = non_answer):

clarification

niceties

refusal_apology

logistics

If accuracy_main is accurate or inaccurate, accuracy_subtype MUST be null.

DECISION ORDER (CRITICAL – FOLLOW STRICTLY)

FIRST determine whether the BOT RESPONSE contains SUBSTANTIVE HEALTH INFORMATION intended to answer the USER MESSAGE.

ONLY IF NO substantive answer exists, classify the response as non_answer.

Politeness, follow-up questions, or closing phrases MUST NOT override a substantive answer.

CRITICAL TURN RULE (OVERRIDES ALL OTHERS)

If the USER MESSAGE is NOT an information-seeking question (for example: Yes, No, Okay, Thanks):

accuracy_main MUST be non_answer
accuracy_subtype MUST be niceties

This applies REGARDLESS of how helpful or correct the BOT RESPONSE is.

STEP 1: SUBSTANTIVE ANSWER EVALUATION

If the BOT RESPONSE provides information intended to answer the USER MESSAGE:

If the information is medically safe, appropriate, relevant, and directly answers the USER MESSAGE:
accuracy_main = accurate
accuracy_subtype = null

If the BOT RESPONSE is medically incorrect, unsafe, misleading, incomplete, OR addresses a DIFFERENT topic, condition, behavior, or scenario than the USER MESSAGE:
accuracy_main = inaccurate
accuracy_subtype = null

IMPORTANT OVERRIDE:
If the BOT RESPONSE answers a DIFFERENT topic than what the USER MESSAGE asked, it MUST be classified as inaccurate, NOT non_answer, even if the response is polite or irrelevant.

STEP 2: NON-ANSWER CLASSIFICATION
(Apply ONLY if NO substantive answer exists)

If the BOT RESPONSE contains ONLY greetings, acknowledgements, or politeness:
accuracy_main = non_answer
accuracy_subtype = niceties

If the BOT RESPONSE explicitly apologizes or states inability to help:
accuracy_main = non_answer
accuracy_subtype = refusal_apology

If the BOT RESPONSE provides operational, administrative, or navigation information instead of health guidance:
accuracy_main = non_answer
accuracy_subtype = logistics

If the BOT RESPONSE ONLY asks for clarification and provides NO health information:
accuracy_main = non_answer
accuracy_subtype = clarification

DANGER-SIGN STRICTNESS (OVERRIDE)

For newborn or preterm danger signs:

Any vague, generic, or incomplete attempt to answer MUST be classified as inaccurate

Do NOT classify such cases as non_answer if an answer attempt is made

INPUT (COPY EXACTLY)

pair_index: {pair_index}
session_id: {session_id}
human_text: {human_text}
ai_text: {ai_text}

OUTPUT JSON FORMAT (STRICT)

{
"pair_index": "<copy exactly>",
"session_id": "<copy exactly or null>",
"human_text": "<copy exactly>",
"ai_text": "<copy exactly>",
"accuracy_main": "<accurate | inaccurate | non_answer>",
"accuracy_subtype": "<clarification | niceties | refusal_apology | logistics | null>",
"rationale": "<one short sentence>"
}

Return ONLY the JSON object.
```

**User message template:**
```
Pair Index (copy exactly):
{{ $json.pair_index }}

USER MESSAGE:
{{ $json.human_text_en }}

BOT RESPONSE:
{{ $json.ai_text }}

SESSION ID:
{{ $json.session_id }}
```

---

### 25. Code in JavaScript (Accuracy Output Parser)
- **Type:** `n8n-nodes-base.code`
- **ID:** `14038ec8-7243-4feb-ad36-6ef34bee0e15`
- **Mode:** runOnceForEachItem
- **Purpose:** Parses the LLM's JSON response from the "Message a model" node. Implements a robust two-path parser:
  1. **Primary:** Extracts `output_text` blocks from the OpenAI response structure, strips BOM and markdown fences, isolates the JSON object, and calls `JSON.parse()`.
  2. **Fallback (regex):** If JSON parsing fails, extracts each expected field using targeted regex patterns to avoid losing data.
- **Output fields:** `pair_index`, `session_id`, `human_text`, `ai_text`, `accuracy_main_llm`, `accuracy_subtype_llm`, `accuracy_rationale_llm`, `llm_parse_mode` ("json" or "regex_fallback"), `llm_parse_error`, `llm_raw_text`.

---

### 26. Edit Fields (Accuracy Column Selector)
- **Type:** `n8n-nodes-base.set`
- **ID:** `d6934eec-abae-41d8-8779-fa51cdaf402a`
- **Purpose:** Selects and renames the accuracy output columns for downstream use: `pair_index`, `session_id`, `human_text`, `ai_text`, `accuracy_main` (from `accuracy_main_llm`), `accuracy_subtype` (from `accuracy_subtype_llm`), `accuracy_rationale` (from `accuracy_rationale_llm`).

---

### 27. Accuracy Analysis Output
- **Type:** `n8n-nodes-base.convertToFile`
- **ID:** `998c63cc-4787-4a0f-ad16-04aaf9e4319b`
- **Purpose:** Saves accuracy results as `Accuracy Analysis Output.csv` (binary). Present for local debugging.

---

### 28. Merge5 (Accuracy + Coverage Join)
- **Type:** `n8n-nodes-base.merge` (combine, enrichInput2, merge by `pair_index`)
- **ID:** `1eb6a9a9-e6ce-49dd-af4c-d4a5567b6e31`
- **Inputs:**
  - Input 0: Accuracy output rows (from "Edit Fields")
  - Input 1: All coverage output rows including unmatched/non-question items (from "Filter columns - Coverage - Accuracy" — the disabled node, or directly from the If node's false branch)
- **Purpose:** Joins accuracy classifications back onto the full coverage dataset so every QnA pair has both dimensions available for the summary generator.

---

### 29. Combined Coverage + Accuracy Output
- **Type:** `n8n-nodes-base.set`
- **ID:** `0984f5d9-ff6c-4d1c-be40-46149594e51c`
- **Purpose:** Final field selector. Produces the canonical combined record per pair: `pair_index`, `session_id`, `human_text`, `ai_text`, `coverage_main`, `coverage_subtype`, `coverage_reason`, `accuracy_main`, `accuracy_subtype`, `accuracy_rationale`.

---

### 30. Overall C+A Summary generator
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `778262cc-f9e5-4789-a1fc-a5469fee1243`
- **Purpose:** Computes the aggregate Coverage and Accuracy summary statistics that appear in the "Overall Analysis Summary" sheet tab. The full computation:

**De-duplication:** Merges rows by `pair_index`, preferring LLM fields (`coverage_main_llm`, `accuracy_main_llm`) over Python fields (`coverage_main`, `accuracy_main`), normalises label variants.

**Coverage metrics (over all pairs):**
- Total pairs
- Matched question count and percentage
- Non-question count
- Unmatched in-scope count
- Unmatched out-of-scope count

**Accuracy metrics (over matched questions only):**
- Accurate, Inaccurate, Non-answer counts
- Non-answer subtypes: clarification, niceties, refusal_apology, logistics
- **Primary KPI:** accurate / (accurate + inaccurate) — "when the bot answers, is it correct?"
- **Secondary KPI:** accurate / total matched questions — "out of questions we claim to cover, how often do users get a correct answer?"

**Output:** Structured rows with `section`, `metric`, `value`, `unit` fields. Sections: Context, Coverage Summary, Coverage Breakdown, Accuracy Summary, Accuracy Breakdown, Notes.

---

### 31. Weekly Activity Summary Generator
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `c498fb88-5f59-4cb4-aa72-88d4d0cff551`
- **Input:** All transcript rows from "Extract from File".
- **Purpose:** Computes per-week engagement statistics. Algorithm:
  1. Parses `Message Date` timestamps and assigns each message to its ISO week (Monday-start).
  2. Tracks `Participant Public ID` (with fallbacks to `Participant Identifier` and `Participant Name`) for user identification.
  3. Tracks which week each user was first seen to determine new vs. returning.
  4. For session duration: iterates consecutive messages within a session; gaps <= 30 minutes contribute to active time; gaps > 30 minutes are discarded. Time is attributed to the week where the gap starts; if a gap crosses a week boundary, it is proportionally split.
- **Output fields per week:** `section` ("WEEKLY_SUMMARY"), `week_start` (ISO date), `weekly_active_users`, `weekly_new_users`, `weekly_returning_users`, `weekly_sessions`, `weekly_total_messages`, `weekly_human_messages`, `weekly_ai_messages`, `weekly_total_session_minutes`, `avg_session_minutes`.

---

### 32. Weekly Session Summary Generator
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `c43eeff9-0fb1-4195-8e88-2f51af0b38bc`
- **Input:** All transcript rows from "Extract from File".
- **Purpose:** Produces one row per unique session with detailed session-level metrics. Algorithm:
  1. Groups messages by `Session ID`.
  2. Sorts messages chronologically within each session.
  3. Computes active duration by summing inter-message gaps <= 30 minutes.
  4. Counts sub-sessions (each gap > 30 minutes starts a new sub-session).
  5. Appends human-readable notes at the bottom of the dataset.
- **Output fields per session:** `section`, `session_id`, `user_id`, `session_start`, `session_end`, `session_created_week`, `session_last_active_week`, `total_messages`, `human_messages`, `ai_messages`, `session_duration_minutes`, `messages_per_minute`, `sub_sessions`.

---

### 33. User Engagement Summary Generator
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `fdf75882-6a51-4cdc-bec3-4d8eb4913b78`
- **Input:** All transcript rows from "Extract from File".
- **Purpose:** Produces a multi-section user engagement report. Four sections:
  1. **USER_ENGAGEMENT_MONTHLY:** Per-month counts of active users broken down by number of active weeks (1 week, 2 weeks, 3 weeks, 4+ weeks); "core users" = those active >= 2 weeks.
  2. **USER_CONCENTRATION_TOP10:** Per-month, how many users are in the top 10% by session count, and what percentage of total sessions they account for. Indicates whether engagement is broadly distributed or dominated by power users.
  3. **USER_ENGAGEMENT_DRILLDOWN:** Per-user lifetime summary: active sessions, weeks, months, last active month. Sorted by most active months first.
  4. **USER_ENGAGEMENT_DRILLDOWN_MONTHLY:** Per-user, per-month: active weeks and sessions. Useful for spotting individual drop-offs.

---

## Spreadsheet Setup (Transcript Pipeline)

### 34. Create spreadsheet
- **Type:** `n8n-nodes-base.googleSheets` (create)
- **ID:** `88e2d3b6-4f82-49cc-b969-7977d402d07b`
- **Credentials:** Google Sheets OAuth2 ("Google Sheets account - Dimagi")
- **Title:** `MBW Chatbot - Coverage & Accuracy Analysis - {{ $now }}`
- **Sheets created:** User Engagement Summary, Weekly Activity Summary, Weekly Session Summary, Overall Analysis Summary, Coverage, Accuracy.

### 35. Move file
- **Type:** `n8n-nodes-base.googleDrive`
- **ID:** `24cd26b8-2bb3-4237-b50d-e1e095b7c93a`
- **Credentials:** Google Drive OAuth2 ("Google Drive account - Dimagi")
- **Destination folder:** "MBW Chatbot Analysis" (ID: `1kaZjtnNeJGAROGlsmqvg7PrJAIJvIQdE`)
- **Purpose:** Moves the newly created spreadsheet to the shared team folder.

### 36. Wait3
- **Type:** `n8n-nodes-base.merge` (chooseBranch)
- **ID:** `66f4c1d9-3b87-4ff1-80f4-de59ee652f4b`
- **Purpose:** Synchronises after both "Create spreadsheet" and "Move file" complete, then triggers "Edit Fields1".

### 37. Edit Fields1
- **Type:** `n8n-nodes-base.set`
- **ID:** `e6d4b246-0b0c-4420-b9f2-2e46c860002a`
- **Purpose:** Extracts `spreadsheetId` and `spreadsheetUrl` from the Google Sheets creation response and propagates them to all downstream write nodes (Merge, Merge1, Merge2, Wait4, Merge3, Merge4, Merge8).

---

## Data Writing to Google Sheets

All write operations follow the same pattern: a Merge node combines the data stream with the spreadsheet URL, appends rows, then deletes the two leftover identifier columns (spreadsheetId, spreadsheetUrl) that n8n auto-includes.

### Coverage Sheet (tab: "Coverage")
- **Merge → Append row in sheet (node ID: `9fa101a2`):** Appends coverage results.
- **Delete rows or columns from sheet (node ID: `de875e6e`):** Removes columns H-I (the two identifier columns).
- **Columns written:** `pair_index`, `human_text`, `ai_text`, `coverage_main_llm`, `coverage_subtype_llm`, `coverage_rationale_llm`, `suggested_qna_topic_llm`, `llm_parse_ok`, `llm_parse_error`, `llm_raw_text_on_fail`.

### Accuracy Sheet (tab: "Accuracy")
- **Merge1 → Append or update row in sheet (node ID: `9a34ee9d`):** Appends accuracy results.
- **Delete rows or columns from sheet1 (node ID: `44684b0c`):** Removes columns H-M.
- **Columns written:** `pair_index`, `session_id`, `human_text`, `ai_text`, `accuracy_main_llm`, `accuracy_subtype_llm`, `accuracy_rationale_llm`, `llm_parse_ok`, `llm_parse_used_regex_fallback`, `llm_parse_error`, `llm_raw_text_on_fail`.

### Weekly Activity Summary Sheet
- **Merge2 → Append row in sheet1 (node ID: `4de05f06`):** Appends weekly activity rows.
- **Delete rows or columns from sheet2 (node ID: `8def4b5c`):** Cleans identifier columns.

### Weekly Session Summary Sheet
- **Merge3 → Append row in sheet2 (node ID: `ff3ea0fe`):** Appends session rows.
- **Delete rows or columns from sheet3 (node ID: `ef911e91`):** Cleans identifier columns.

### Overall Analysis Summary Sheet
- **Merge4 → Append row in sheet3 (node ID: `7329422f`):** Appends summary rows.
- **Delete rows or columns from sheet4 (node ID: `1883861b`):** Cleans identifier columns.

### User Engagement Summary Sheet
- **Merge8 → Append row in sheet4 (node ID: `4c490bd3`):** Appends engagement rows.
- **Delete rows or columns from sheet5 (node ID: `2dfd9694`):** Cleans identifier columns.

---

## Synchronisation Chain (Wait Nodes)

The pipeline uses a chain of `chooseBranch` merge nodes to sequence all sheet-write operations. This prevents race conditions when writing to different tabs of the same spreadsheet.

```
Wait1 (Coverage + Accuracy done)
  → Wait2 (Weekly Activity done)
    → Wait4 (Coverage+Accuracy columns cleaned)
      → Wait5 (Weekly Session done)
        → Wait6 (Overall Summary done)
          → Wait7 (User Engagement done)
            → Send email
```

---

### 38. Send email: Coverage Analysis O/P
- **Type:** `n8n-nodes-base.emailSend`
- **ID:** `7a613be0-89ca-4bd8-897d-c80812658202`
- **Credentials:** SMTP ("SMTP account - Dimagi")
- **From:** `asidtharthan@dimagi.com`
- **To:** The email address submitted in the form (`$items("Chatbot Analysis Trigger")[0].json["Email ID"]`)
- **Subject:** `MBW UAT: Coverage & Accuracy Analysis Results`
- **Body (HTML):**
  ```
  Your Coverage & Accuracy analysis is complete.

  Google Sheet Url:
  {{ $json.spreadsheetUrl }}
  ```

---

## Complete Flow Diagram

```
Chatbot Analysis Trigger (form)
  |
  +-- Extract from File (transcript CSV/XLSX)
  |     |
  |     +-- QnA Pairing (JS: form human→AI pairs)
  |     |     └── Convert to File (CSV)
  |     |           └── Extract pair o/ps
  |     |                 |
  |     |                 +-- Translate a language (Google Translate → English)
  |     |                 |     └── Code in JavaScript1 (clean translation, extract pair_index)
  |     |                 |           └── Merge7 (join translated text back onto pairs) ──┐
  |     |                 +-- Merge7 (input 0) ───────────────────────────────────────────┘
  |     |                       └── Edit Fields2 (select key fields)
  |     |                             └── Merge6 (wait for Bot field)
  |     |                                   └── Wait (wait for QnA bank)
  |     |                                         └── Coverage Analysis: JS (Jaccard matching)
  |     |                                               |
  |     |                                               +-- If (coverage_main == "Matched question"?)
  |     |                                               |     |
  |     |                                               |     +-- TRUE: Message a model (OpenAI accuracy LLM)
  |     |                                               |     |         └── Code in JavaScript (parse LLM output)
  |     |                                               |     |               └── Edit Fields (rename accuracy cols)
  |     |                                               |     |                     └── Merge5 (join with false branch)
  |     |                                               |     |                           └── Combined Coverage+Accuracy Output
  |     |                                               |     |                                 └── Overall C+A Summary generator
  |     |                                               |     |                                       └── [to spreadsheet write chain]
  |     |                                               |     └── FALSE: Merge5 (input 1)
  |     |
  |     +-- Weekly Activity Summary Generator (JS)  ─┐
  |     +-- Weekly Session Summary Generator (JS)   ─+── [to spreadsheet write chain]
  |     +-- User Engagement Summary Generator (JS)  ─┘
  |
  +-- Extract qna bank o/ps ─────────────────────────── Wait (input 1)
  |
  +-- Create spreadsheet (Google Sheets)
  |     └── Move file (Google Drive → MBW Chatbot Analysis folder)
  |           └── Wait3 → Edit Fields1 (propagate spreadsheet URL to all writers)
  |
  +-- Edit Fields3 (Bot field) ────────────────────────── Merge6 (input 1)

[Spreadsheet write chain]
  Edit Fields1 (spreadsheetUrl)
    → Merge / Merge1 / Merge2 / Merge3 / Merge4 / Merge8 (combine with data)
      → Append rows to each sheet tab
        → Delete identifier columns
          → Wait chain (serialise writes)
            → Send email: Coverage Analysis O/P
```

---

## External Services and Credentials

| Service | Purpose | Credential Name |
|---|---|---|
| Google Translate API | Translates user messages to English | "Google Translate account" |
| OpenAI API | Accuracy classification (GPT-4.1-nano) | "OpenAi account" |
| Google Sheets API | Creates and writes output spreadsheet | "Google Sheets account - Dimagi" |
| Google Drive API | Moves spreadsheet to shared folder | "Google Drive account - Dimagi" |
| SMTP (email) | Sends completion notification | "SMTP account - Dimagi" |

---

## Output Google Sheet Structure

The output spreadsheet is titled `MBW Chatbot - Coverage & Accuracy Analysis - <timestamp>` and contains six tabs:

| Tab | Contents |
|---|---|
| User Engagement Summary | Monthly engagement, top-10% concentration, per-user lifetime and monthly drilldowns |
| Weekly Activity Summary | Per-week: active/new/returning users, sessions, messages, session minutes |
| Weekly Session Summary | Per-session: user, start/end, duration, message counts, sub-sessions |
| Overall Analysis Summary | Aggregated Coverage + Accuracy KPIs with notes |
| Coverage | Per-pair: coverage classification, subtype, rationale, suggested QnA topic |
| Accuracy | Per-pair (matched only): accuracy classification, subtype, rationale |

---

## Key Design Decisions

1. **JavaScript-first coverage matching** replaces the earlier OpenAI-based approach (which is preserved but disabled). The Jaccard similarity engine is faster, cheaper, and deterministic.
2. **FLW-specific normalisation** strips teaching-wrapper phrasing before matching, since FLWs phrase questions as "how do I explain X to a mother" rather than asking directly.
3. **Translation to English** before coverage matching allows the pipeline to handle multilingual transcripts (Hausa, Pidgin, etc.) against an English QnA bank.
4. **Accuracy LLM is only called for matched questions** — this reduces API cost and avoids meaningless evaluations for greetings or out-of-scope messages.
5. **Danger-sign strictness** is explicitly encoded in the accuracy prompt: vague or generic answers to questions about newborn/preterm danger signs must be classified as inaccurate, not non-answer.
6. **Serialised sheet writes** using a Wait-node chain prevent concurrent write conflicts in the Google Sheets API.
7. **Regex fallback parser** for LLM output ensures the pipeline never loses data even if the model returns slightly malformed JSON.
