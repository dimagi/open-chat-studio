## Login Credentials
Username: tester@playwright.com
Password: My0riginalP@ssw0rd!

# Core Workflows

## 1. Chatbot Management

### Create a Chatbot
1. Click on "Chatbots" in the sidebar
2. Click "Add New" button at the top right
3. In the dialog, fill in "Name" and optionally "Description"
4. Click "Create Chatbot"
5. Verify the chatbot appears in the list table (shows Name, Total Participants, Total Sessions, Total Interactions, Last Activity, Trends, Actions)

### View Chatbot Details
1. Click on a chatbot name in the list
2. Verify the detail page shows: chatbot name, description, channels section, and tabs (Sessions, Versions, Settings)
3. Actions available: "Chat to the bot", "Edit" (opens pipeline editor), "Copy"

### Edit Chatbot Pipeline
1. From the chatbot detail page, click "Edit" (pencil icon)
2. Pipeline editor opens with a React Flow canvas showing Input, processing nodes (e.g., LLM), and Output
3. Click the "+" button to add new nodes
4. Configure nodes by editing their inline properties (e.g., prompt text, history mode)
5. Click "Advanced" on an LLM node to see full configuration (model selection, etc.)
6. Click the save button (lock icon, bottom-left of canvas)

### Edit Chatbot Settings
1. From the chatbot detail page, click the "Settings" tab
2. Click the "Edit" button
3. Modify settings:
   - Name and Description
   - Speech: Provider, Synthetic Voice, Response Type (Always/Reciprocal/Never), Echo Transcript
   - Tracing: Provider, Debug Mode
   - Consent: Conversational consent toggle, Consent Form selection
   - Surveys: Pre-survey and Post-survey dropdowns
   - Participant Allowlist: Add/remove identifiers (supports E164 phone format)
   - Seed Message
   - Web Chat Features: File uploads toggle
4. Click "Save"

### Archive a Chatbot
1. From the chatbot detail page, click the "Settings" tab
2. Click the "Archive" button
3. Confirm the chatbot is removed from the chatbot list, but is shown when the "Show Archived" toggle at the top of the list it ON

### Copy a Chatbot
1. From the chatbot detail page, click the "Copy" button in the header area
2. A "Copy Chatbot" dialog appears with a "New Chatbot Name" field pre-filled with "{original name} (copy)"
3. Optionally modify the name
4. Click "Confirm"
5. Verify redirect to the new chatbot's detail page
6. Navigate back to the chatbots list and verify the copied chatbot appears in the table

---

## 2. Chatbot Versions

### View Versions
1. From the chatbot detail page, click the "Versions" tab
2. View the version table: version number, created date, description, published status, archived status

### Create a Version
1. From the Versions tab, click "+ Create Version"
2. Fill in version details and submit
3. The button changes to "Creating Version" (disabled) while the version is being created asynchronously
4. Wait a few seconds for the version to appear in the versions table without reloading the page. If this takes more than 10 seconds, this test fails.
5. Verify the new version row shows: version number, created date, description, published status, and archived status

---

## 3. Channels

### Copy API Channel URL
1. From the chatbot detail page, click the "API" channel button
2. The API URL is copied to clipboard (button text changes to "Copied!")

### Access Web Channel
1. From the chatbot detail page, click the "Web" channel button

### Add a New Channel
1. From the chatbot detail page, click the "+" button next to existing channels
2. Note: WhatsApp, Facebook, Sureadhere channels require configuring the respective messaging provider first on the team page
3. For each inaccessible channel (WhatsApp, Facebook, Sureadhere):
   a. Navigate to the team page
   b. Under "Messaging Providers", click "Add new"
   c. Configure the respective provider (WhatsApp, Facebook, or Sureadhere) with required fields
   d. Click "Create"
   e. Return to the chatbot detail page
   f. Click the "+" button and verify the previously inaccessible channel is now available

---

## 4. Sessions

### View All Sessions (Global)
1. Navigate to "All sessions" in the sidebar (under Chatbots)
2. Verify the sessions table is visible with expected columns

### View Chatbot Sessions
1. From the chatbot detail page, click the "Sessions" tab
2. View the sessions table: Participant, Message Count, Last Activity, Tags, Versions, State, Remote Id
3. Click "Session Details" link for a specific session

### View Session Details
1. From a session list, click "Session Details"
2. View session metadata: Participant, Remote ID, Status, Start/End times, Chatbot, Platform, Tags, Comments
3. View tabs: Messages, Participant Data, Participant Schedules, Session State, Chatbot Events

### End a Session
1. From the session list, click "Session Details"
2. From the session detail page, click "End Session"

### Start New Session (from Session Detail)
1. From the session detail page, click "New Session"
2. Expect session status to be "awaiting final review"

### Generate Chat Export
1. From the chatbot's sessions tab, click "Generate Chat Export"
2. Expect report to be downloadable after a few seconds
