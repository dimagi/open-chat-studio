# Chatbot Engagement Analysis Pipeline: Workflow Analysis

## Workflow Overview

**Trigger node name:** "Overall Bot Summary Analysis"
**Form title:** "Chatbot Engagement Analysis Pipeline: Upload"
**Purpose:** Automated user engagement metrics generation for the MBW (Mother and Baby Wellness) chatbot. This pipeline ingests a raw chatbot transcript file, computes three categories of engagement statistics (weekly activity summary, per-session details, and user engagement/retention), writes all results to a new Google Sheets workbook with three tabs, and sends a completion notification email. Unlike the Transcript Analysis Pipeline, this workflow does **not** perform any LLM-based analysis or coverage/accuracy classification — it is purely a data aggregation and reporting pipeline.

---

## Input

The trigger is an n8n web form that collects:

| Field | Type | Details |
|---|---|---|
| Question Bank | File upload | `.xlsx`, `.xls`, or `.csv` — collected by the form but not used in this pipeline's processing logic |
| Transcript | File upload | `.xlsx`, `.xls`, or `.csv`; raw chatbot conversation export with message rows |
| Email ID | Email | Recipient for the output notification |
| Project | Dropdown | Fixed to "Weekly Overview Generation" |
| Bot | Dropdown | "FLW" (Front Line Worker) or "Mother" — collected but not used in computation |

---

## Node-by-Node Description

### 1. Overall Bot Summary Analysis
- **Type:** `n8n-nodes-base.formTrigger`
- **ID:** `bb7931db-dddc-40c7-8d63-6c3ba5c1f673`
- **Purpose:** Presents the upload form. On submission, fires two parallel branches: transcript file extraction and spreadsheet creation.

---

### 2. Extract from File1
- **Type:** `n8n-nodes-base.extractFromFile`
- **ID:** `cc96a43e-3043-4014-b989-9b864f8e43ee`
- **Input:** The binary `Transcript` file attachment from the form trigger.
- **Purpose:** Parses the uploaded transcript file (CSV or Excel) into one JSON item per message row. Expected columns include `Message Date`, `Message Type` (human/ai), `Session ID`, `Participant Public ID`, `Participant Identifier`, and `Participant Name`.
- **Output:** One item per message row, fanning out in parallel to three analysis generators: Weekly Activity Summary Generator1, Weekly Session Summary Generator1, and User Engagement Summary Generator1.

---

### 3. Weekly Activity Summary Generator1
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `2f07b4bb-6bdb-4933-a804-ac68f61686b1`
- **Input:** All transcript rows from "Extract from File1".
- **Purpose:** Computes per-week aggregated engagement statistics across the entire transcript date range.

**Field mapping used:**
- Timestamp: `Message Date`
- Message type: `Message Type` (values: "human" or "ai")
- Session identifier: `Session ID`
- User identifier (in priority order): `Participant Public ID` → `Participant Identifier` → `Participant Name` → "UNKNOWN_USER"

**Algorithm:**

1. **Week assignment:** Each message's timestamp is converted to its ISO week start (Monday, UTC). Week boundaries are computed as Monday 00:00:00 UTC.

2. **User tracking:** The first week a user is seen is recorded. In any subsequent week, they are counted as "returning"; in their first week they are "new".

3. **Session duration (week-aware split):** For each session, consecutive message pairs are examined:
   - Gaps > 30 minutes are treated as inactivity gaps and excluded from active time.
   - Gaps <= 30 minutes contribute to `session_duration_minutes`.
   - If an active gap crosses a Monday week boundary, it is proportionally split: time before Monday is credited to the earlier week, time after Monday is credited to the later week.

4. **Per-week aggregation:**
   - `weekly_active_users`: distinct user IDs with any message in this week
   - `weekly_new_users`: users for whom this week is their global first-seen week
   - `weekly_returning_users`: active - new
   - `weekly_sessions`: distinct session IDs that had active time attributed to this week
   - `weekly_total_messages`: count of all messages (human + AI)
   - `weekly_human_messages`: count of human messages
   - `weekly_ai_messages`: count of AI messages
   - `weekly_total_session_minutes`: sum of active gap durations attributed to this week (rounded to 1 decimal)
   - `avg_session_minutes`: total session minutes / session count (0 if no sessions)

5. Results are sorted chronologically by week start date.

**Output fields per row:** `section` ("WEEKLY_SUMMARY"), `week_start` (YYYY-MM-DD), `weekly_active_users`, `weekly_new_users`, `weekly_returning_users`, `weekly_sessions`, `weekly_total_messages`, `weekly_human_messages`, `weekly_ai_messages`, `weekly_total_session_minutes`, `avg_session_minutes`.

---

### 4. Weekly Session Summary Generator1
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `8c132019-ae81-4df6-ac52-8477f4c8dd26`
- **Input:** All transcript rows from "Extract from File1".
- **Purpose:** Produces one data row per unique conversation session with detailed timing and message metrics.

**Field mapping:** Same as Weekly Activity Summary Generator1.

**Algorithm:**

1. Groups all messages by `Session ID`. Within each session, messages are sorted chronologically.

2. For each session:
   - `session_start` / `session_end`: ISO timestamps of first/last message.
   - `session_created_week` / `session_last_active_week`: ISO week starts (Monday-based) for first and last message.
   - `session_duration_minutes`: sum of inter-message gaps <= 30 minutes, rounded to 1 decimal.
   - `human_messages`, `ai_messages`, `total_messages`: message type counts.
   - `messages_per_minute`: total messages / duration (0 if duration is 0).
   - `sub_sessions`: number of continuous activity blocks within the session. Starts at 1; increments by 1 for each gap > 30 minutes encountered.

3. Rows are sorted first by `session_created_week`, then by `session_start`.

4. After all session rows, a spacer row and four explanatory note rows are appended at the bottom of the output to document the methodology for spreadsheet readers:
   - "Each row represents one unique session."
   - "Sessions may span multiple weeks..."
   - "sub_sessions = number of continuous activity blocks..."

**Output fields per session row:** `section` ("SESSION_DETAILS"), `session_id`, `user_id`, `session_start`, `session_end`, `session_created_week`, `session_last_active_week`, `total_messages`, `human_messages`, `ai_messages`, `session_duration_minutes`, `messages_per_minute`, `sub_sessions`.

---

### 5. User Engagement Summary Generator1
- **Type:** `n8n-nodes-base.code` (JavaScript)
- **ID:** `d1fd170e-6795-47f2-aa6c-71f2b072ef21`
- **Input:** All transcript rows from "Extract from File1".
- **Purpose:** Produces a multi-section user engagement and retention report across four sections, interleaved in the output with separator rows between sections.

**Configuration:** Top-10% threshold = 10% (`TOP_PCT = 0.10`)

**Field mapping:** Same as above.

**Section 1 — USER_ENGAGEMENT_MONTHLY (Monthly Engagement)**
Header rows followed by one data row per calendar month (YYYY-MM format, UTC):
- `total_active_users`: distinct users with any message in the month
- `core_users_2plus_weeks`: users active in 2 or more distinct weeks
- `users_1_week_active` through `users_4plus_weeks_active`: count of users active in exactly 1, 2, 3, or 4+ distinct weeks within the month

*Section note (written to header row):* "Monthly view of how many users were active and how consistently they engaged across weeks."

**Section 2 — USER_CONCENTRATION_TOP10 (Power-User Concentration)**
Header rows followed by one row per calendar month:
- `top_10pct_users`: count of users in the top 10% by session count in that month (minimum 1)
- `pct_sessions_by_top_10pct_users`: percentage of that month's sessions accounted for by the top-10% users

*Section note:* "Shows whether engagement is evenly distributed or dominated by a small group of heavy users."

**Section 3 — USER_ENGAGEMENT_DRILLDOWN (Per-User Lifetime Summary)**
Header rows followed by one row per distinct user, sorted by descending `active_months`:
- `user_id`
- `active_sessions`: total distinct session IDs across all time
- `active_weeks`: total distinct week starts the user was active
- `active_months`: total distinct months the user was active
- `last_active_month`: most recent YYYY-MM in which the user had activity

*Section note:* "User-level lifetime engagement summary across sessions, weeks, and months."

**Section 4 — USER_ENGAGEMENT_DRILLDOWN_MONTHLY (Per-User Monthly)**
Header rows followed by one row per (user, month) combination:
- `user_id`, `month`, `active_weeks`, `active_sessions`

*Section note:* "Month-by-month activity for each user to understand engagement timing and drop-offs."

**Output structure:** section1 rows + separator + section2 rows + separator + section3 rows + separator + section4 rows. Each separator is `{ section: "***" }`.

---

## Spreadsheet Setup

### 6. Create spreadsheet1
- **Type:** `n8n-nodes-base.googleSheets` (create)
- **ID:** `94b0b02a-c8f1-4be7-bb26-f6b316a7906e`
- **Credentials:** Google Sheets OAuth2 ("Google Sheets account - Dimagi")
- **Title:** `MBW Chatbot - Coverage & Accuracy Analysis - {{ $now }}`
- **Sheets created:** User Engagement Summary, Weekly Activity Summary, Weekly Session Summary.
- **Note:** The spreadsheet title retains the "Coverage & Accuracy Analysis" prefix from the transcript pipeline template, but this pipeline only populates the three engagement tabs.

---

### 7. Move file1
- **Type:** `n8n-nodes-base.googleDrive`
- **ID:** `b7deb030-791c-4933-9355-3f819cb83806`
- **Credentials:** Google Drive OAuth2 ("Google Drive account - Dimagi")
- **Destination folder:** "MBW Chatbot Analysis" (folder ID: `1kaZjtnNeJGAROGlsmqvg7PrJAIJvIQdE`)
- **Purpose:** Moves the newly created spreadsheet from the default Google Drive root into the shared team folder immediately after creation.
- **Runs once:** `executeOnce: true`.

---

### 8. Wait9
- **Type:** `n8n-nodes-base.merge` (chooseBranch)
- **ID:** `6e39adff-bc3f-4ba7-b927-afea9c6a1cb3`
- **Purpose:** Synchronisation gate. Waits for both "Create spreadsheet1" (input 0) and "Move file1" (input 1) to complete before proceeding to "Edit Fields4". Uses `chooseBranch` so it passes data from the primary input.

---

### 9. Edit Fields4
- **Type:** `n8n-nodes-base.set`
- **ID:** `01f695a1-6b83-447d-8f87-0b67729c900f`
- **Purpose:** Extracts `spreadsheetId` and `spreadsheetUrl` from the Google Sheets create response and propagates them to all downstream write nodes: Merge9 (input 0), Wait10 (input 1), Merge10 (input 0), and Merge11 (input 0).

---

## Data Writing to Google Sheets

Each write follows the same pattern: a Merge node combines the analysis data stream with the spreadsheet URL signal, appends all rows to the target sheet, then deletes the first two columns (`spreadsheetId`, `spreadsheetUrl`) that n8n includes automatically.

### Weekly Activity Summary Tab

**9a. Merge9**
- **Type:** `n8n-nodes-base.merge` (combineAll)
- **ID:** `0067ec50-e9c4-4166-8008-04f71ddd02d3`
- **Inputs:** Edit Fields4 (spreadsheet URL, input 0) + Weekly Activity Summary Generator1 output (input 1).

**9b. Append row in sheet5**
- **Type:** `n8n-nodes-base.googleSheets` (append)
- **ID:** `e3b3a81d-8eb5-48e1-9f96-5d1fd0660349`
- **Target sheet tab:** "Weekly Activity Summary"
- **Columns written:** `spreadsheetId`, `spreadsheetUrl`, `section`, `week_start`, `weekly_active_users`, `weekly_new_users`, `weekly_returning_users`, `weekly_sessions`, `weekly_total_messages`, `weekly_human_messages`, `weekly_ai_messages`, `weekly_total_session_minutes`, `avg_session_minutes`.

**9c. Delete rows or columns from sheet6**
- **Type:** `n8n-nodes-base.googleSheets` (delete columns)
- **ID:** `78259cdc-09bc-47db-982b-66133298b96e`
- **Removes:** 2 columns starting from column A (the `spreadsheetId` and `spreadsheetUrl` columns).
- **Runs once:** `executeOnce: true`.

---

### Weekly Session Summary Tab

**10a. Merge10**
- **Type:** `n8n-nodes-base.merge` (combineAll)
- **ID:** `2c834615-f5b3-492e-81b1-914292f5dde5`
- **Inputs:** Edit Fields4 (input 0) + Weekly Session Summary Generator1 output (input 1).

**10b. Append row in sheet6**
- **Type:** `n8n-nodes-base.googleSheets` (append)
- **ID:** `93694227-f4cf-4136-949e-7b8aa66a2458`
- **Target sheet tab:** "Weekly Session Summary"
- **Columns written:** `spreadsheetId`, `spreadsheetUrl`, `section`, `session_id`, `user_id`, `session_start`, `session_end`, `session_created_week`, `session_last_active_week`, `total_messages`, `human_messages`, `ai_messages`, `session_duration_minutes`, `messages_per_minute`, `sub_sessions`.

**10c. Delete rows or columns from sheet7**
- **Type:** `n8n-nodes-base.googleSheets` (delete columns)
- **ID:** `d80b2c7f-78d1-4bc0-97ab-dff7309737de`
- **Removes:** 2 columns.
- **Runs once.**

---

### User Engagement Summary Tab

**11a. Merge11**
- **Type:** `n8n-nodes-base.merge` (combineAll)
- **ID:** `b4ad3d04-61ac-48d3-946b-cc01a0f8bd6d`
- **Inputs:** Edit Fields4 (input 0) + User Engagement Summary Generator1 output (input 1).

**11b. Append row in sheet7**
- **Type:** `n8n-nodes-base.googleSheets` (append)
- **ID:** `17990a9a-5377-4471-9c9d-1baf1889f3d5`
- **Target sheet tab:** "User Engagement Summary"
- **Columns written:** `spreadsheetId`, `spreadsheetUrl`, `section`, `month`, `total_active_users`, `core_users_2plus_weeks`, `users_1_week_active`, `users_2_weeks_active`, `users_3_weeks_active`, `users_4plus_weeks_active`, `top_10pct_users`, `pct_sessions_by_top_10pct_users`, `user_id`, `active_sessions`, `active_weeks`, `active_months`, `last_active_month`.

**11c. Delete rows or columns from sheet8**
- **Type:** `n8n-nodes-base.googleSheets` (delete columns)
- **ID:** `a3fd11b0-278f-46d6-9a26-60f0d84f2cec`
- **Removes:** 2 columns.
- **Runs once.**

---

## Synchronisation Chain (Wait Nodes)

The three sheet write operations are sequenced to avoid Google Sheets API race conditions:

```
Edit Fields4 (provides spreadsheetUrl)
  |
  +-- Merge9 → Append row in sheet5 (Weekly Activity)
  |     └── Delete rows or columns from sheet6
  |           └── Wait10 (input 0) ──────────────────────┐
  |                                                       |
  +-- [Wait10 input 1 from Edit Fields4] ────────────────┘
        └── Wait10 output
              └── Wait11 (input 0) ─────────────────────┐
                                                         |
  +-- Merge10 → Append row in sheet6 (Weekly Session)    |
  |     └── Delete rows or columns from sheet7           |
  |           └── Wait11 (input 1) ─────────────────────┘
  |                 └── Wait11 output
  |                       └── Wait12 (input 0) ─────────┐
  |                                                      |
  +-- Merge11 → Append row in sheet7 (User Engagement)   |
        └── Delete rows or columns from sheet8           |
              └── Wait12 (input 1) ──────────────────────┘
                    └── Wait12 output
                          └── Send email: Coverage Analysis O/P1
```

### Wait10
- **Type:** `n8n-nodes-base.merge` (chooseBranch, useDataOfInput: 2)
- **ID:** `fe202086-99bc-4125-8fb2-4f8e287fd856`
- **Purpose:** Waits for the Weekly Activity write + column cleanup to complete (input 0 from "Delete rows or columns from sheet6") and for the spreadsheet URL signal (input 1 from "Edit Fields4") before proceeding to Wait11.

### Wait11
- **Type:** `n8n-nodes-base.merge` (chooseBranch)
- **ID:** `f0dffb36-674e-451a-a1d8-2e3b978b2bce`
- **Purpose:** Waits for Wait10 (input 0) and the Weekly Session write cleanup (input 1 from "Delete rows or columns from sheet7") before proceeding to Wait12.

### Wait12
- **Type:** `n8n-nodes-base.merge` (chooseBranch)
- **ID:** `8bc8d79e-161d-4c00-a147-45f865964bb0`
- **Purpose:** Waits for Wait11 (input 0) and the User Engagement write cleanup (input 1 from "Delete rows or columns from sheet8") before triggering the email send.

---

### Send email: Coverage Analysis O/P1
- **Type:** `n8n-nodes-base.emailSend`
- **ID:** `28a4d157-1a43-4790-80b2-516e0d2f15d0`
- **Credentials:** SMTP ("SMTP account - Dimagi")
- **From:** `asidtharthan@dimagi.com`
- **To:** The email address submitted in the form (`$items("Overall Bot Summary Analysis")[0].json["Email ID"]`)
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
Overall Bot Summary Analysis (form trigger)
  |
  +-- Extract from File1 (transcript CSV/XLSX → JSON rows)
  |     |
  |     +-- Weekly Activity Summary Generator1 (JS)
  |     |     └── Merge9 (combine with spreadsheet URL)
  |     |           └── Append row in sheet5 (Weekly Activity Summary tab)
  |     |                 └── Delete rows or columns from sheet6 (clean identifier cols)
  |     |                       └── Wait10 (input 0)
  |     |
  |     +-- Weekly Session Summary Generator1 (JS)
  |     |     └── Merge10 (combine with spreadsheet URL)
  |     |           └── Append row in sheet6 (Weekly Session Summary tab)
  |     |                 └── Delete rows or columns from sheet7 (clean identifier cols)
  |     |                       └── Wait11 (input 1)
  |     |
  |     +-- User Engagement Summary Generator1 (JS)
  |           └── Merge11 (combine with spreadsheet URL)
  |                 └── Append row in sheet7 (User Engagement Summary tab)
  |                       └── Delete rows or columns from sheet8 (clean identifier cols)
  |                             └── Wait12 (input 1)
  |
  +-- Create spreadsheet1 (Google Sheets: create new workbook)
        └── Move file1 (Google Drive: move to MBW Chatbot Analysis folder)
              └── Wait9 (sync both done)
                    └── Edit Fields4 (extract + propagate spreadsheetUrl)
                          |
                          +──> Merge9 (input 0)
                          +──> Merge10 (input 0)
                          +──> Merge11 (input 0)
                          +──> Wait10 (input 1)

Wait10 → Wait11 → Wait12 → Send email: Coverage Analysis O/P1
```

---

## External Services and Credentials

| Service | Purpose | Credential Name |
|---|---|---|
| Google Sheets API | Creates the output spreadsheet and writes all tabs | "Google Sheets account - Dimagi" |
| Google Drive API | Moves the spreadsheet to the shared team folder | "Google Drive account - Dimagi" |
| SMTP (email) | Sends the completion notification to the submitter | "SMTP account - Dimagi" |

No LLM or translation services are used in this pipeline.

---

## Output Google Sheet Structure

The output spreadsheet is titled `MBW Chatbot - Coverage & Accuracy Analysis - <timestamp>` and contains three tabs:

| Tab | Contents | Key Metrics |
|---|---|---|
| Weekly Activity Summary | One row per ISO week (Monday-start) | active/new/returning users, sessions, messages, session minutes |
| Weekly Session Summary | One row per session + explanatory notes at bottom | session start/end, duration, message counts, sub-session count |
| User Engagement Summary | Four sections: monthly engagement, top-10% concentration, per-user lifetime, per-user monthly | retention tiers, power-user concentration, per-user drilldown |

---

## Differences from the Transcript Analysis Pipeline

| Aspect | Engagement Pipeline | Transcript Analysis Pipeline |
|---|---|---|
| Trigger form title | "Chatbot Engagement Analysis Pipeline: Upload" | "Chatbot Transcript Analysis Pipeline: Upload" |
| Trigger node name | "Overall Bot Summary Analysis" | "Chatbot Analysis Trigger" |
| QnA bank used | Uploaded but not used | Used for Jaccard matching |
| LLM calls | None | OpenAI GPT-4.1-nano for accuracy classification |
| Translation | Not performed | Google Translate to English |
| Output tabs | 3 (engagement only) | 6 (engagement + coverage + accuracy + overall summary) |
| Project dropdown | "Weekly Overview Generation" | "MBW Chatbot Transcript Analysis" |
| Bot-type-specific logic | Not applied | FLW prefix stripping in Coverage Analysis |

The engagement analysis generators (Weekly Activity, Weekly Session, User Engagement) use **identical JavaScript code** in both pipelines. The Engagement Pipeline isolates and runs just those three generators; the Transcript Pipeline runs the same generators as a secondary sub-component alongside its QA analysis.

---

## Key Design Decisions

1. **No LLM calls** — this pipeline is entirely deterministic JavaScript, making it fast, cheap, and reproducible.
2. **30-minute inactivity threshold** for session duration calculation is hard-coded in both session generators. Gaps longer than 30 minutes are excluded from duration but do increment the sub-session counter.
3. **Week boundaries are Monday-based** (ISO week standard), computed in UTC to avoid timezone ambiguity.
4. **User identification fallback chain** (`Participant Public ID` → `Participant Identifier` → `Participant Name`) handles different export formats from the chatbot platform.
5. **Session duration splits across week boundaries** — active gaps that cross Monday midnight are proportionally attributed to each week, ensuring weekly session-minute totals are accurate even for long sessions.
6. **Serialised sheet writes** prevent concurrent API conflicts despite the three analysis generators running in parallel.
7. **Column cleanup step** after each sheet append removes the `spreadsheetId`/`spreadsheetUrl` columns that n8n automatically includes, keeping the sheets clean for end-users.
