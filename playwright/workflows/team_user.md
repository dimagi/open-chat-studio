## Login Credentials
Username: tester@playwright.com
Password: My0riginalP@ssw0rd!

# Core Workflows

## 1. Authentication

### Sign In
1. Navigate to the app root URL
2. Click "Sign In" on the landing page (or go to `/accounts/login/`)
3. Fill in the "Email" field
4. Fill in the "Password" field
5. Click "Sign In" button
6. Verify redirect to the team dashboard

### Sign Out
1. Click "Sign out" in the sidebar under "My Account"
2. Verify redirect to the landing page

### Change Password
1. Navigate to "Change Password" in the sidebar under "My Account"
2. Fill in current and new password fields
3. Submit the form

---

## 2. Team Management (via `/a/{team-slug}/team/`)

### Edit Team Name
1. Navigate to the team page
2. Update the "Team Name" field
3. Click "Save"

### Invite a Team Member
1. Navigate to the team page
2. Scroll to "Invite Team Members"
3. Fill in the "Email" field
4. Select one or more role checkboxes (Super Admin, Team Admin, Experiment Admin, Chat Viewer, Assistant Admin, Event Admin, Pipeline Admin, Evaluation Admin, Annotation Reviewer)
5. Click "Send Invitation"
6. Verify the invitation appears in the pending invitations list

### Manage Feature Flags
1. Navigate to the team page
2. Click "Manage Feature Flags"
3. Toggle the desired flags (events, evaluations, human_annotations)
4. Click "Save Changes"
5. Verify the flags are saved

### Delete Team
1. Navigate to the team page
2. Scroll to "Danger Zone"
3. Click "Delete Team"
4. Confirm the deletion

---

## 3. Service Providers (all accessible from the team page)

### Add an LLM Provider
1. Navigate to the team page
2. Under "LLM and Embedding Model Service Providers", click "Add new"
3. Fill in "Name"
4. Select the provider "Type" (OpenAI, Azure OpenAI, Anthropic, Groq, Perplexity, DeepSeek, Google Gemini, Google Vertex AI)
5. Review the default LLM models list
6. Add custom LLM models by clicking "+"
7. Fill in "API Key" using a mock key
8. Optionally fill in "API Base URL" and "Organization ID"
9. Click "Create"
10. Verify the provider appears in the team page list

### Add a Speech Provider
1. Navigate to the team page
2. Under "Speech Service Providers", click "Add new"
3. Fill in the required fields
4. Click "Create"

### Add a Messaging Provider
1. Navigate to the team page
2. Under "Messaging Providers", click "Add new"
3. Fill in the required fields
4. Click "Create"

### Add an Authentication Provider
1. Navigate to the team page
2. Under "Authentication Providers", click "Add new"
3. Fill in the required fields
4. Click "Create"

### Add a Tracing Provider
1. Navigate to the team page
2. Under "Tracing Providers", click "Add new"
3. Fill in the required fields
4. Click "Create"

### Delete Providers
1. For each provider type (LLM, Speech, Tracing etc), delete it
2. Confirm all deleted providers are removed from the table.
---

## 4. Custom Actions (accessible from team page)

### Create a Custom Action
1. Navigate to the team page
2. Under "Custom Actions", click "Add new"
3. Fill in "Name" and "Description"
4. Optionally select an "Auth" provider
5. Optionally fill in "Additional Prompt"
6. Fill in "Base URL"
7. Optionally fill in "Health Check Path"
8. Fill in "API Schema" (JSON, defaults to `{}`)
9. Click "Create"

---

## 5. Chatbot Management

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

### Search/Filter Chatbots
1. Navigate to "Chatbots" in the sidebar
2. Use the search box to filter by name

---

## 6. Chatbot Versions

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

## 7. Channels

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

## 8. Sessions

### View All Sessions (Global)
1. Navigate to "All sessions" in the sidebar (under Chatbots)
2. Use Filter and Date Range controls to narrow results
3. Click "Filter", select the "Status" column, set operator to "any of", and select "complete"
4. Verify only completed sessions are shown (or "No sessions yet!" if none match)
5. Remove the filter, add a "Chatbot" filter, select "Customer Support Bot"
6. Verify only sessions belonging to "Customer Support Bot" are shown

### View Chatbot Sessions
1. From the chatbot detail page, click the "Sessions" tab
2. View the sessions table: Participant, Message Count, Last Activity, Tags, Versions, State, Remote Id
3. Use Filter and Date Range controls
4. Click "Filter", select the "Status" column, set operator to "any of", and select "active"
5. Verify only active sessions are shown (or "No sessions yet!" if none match)
6. Remove the filter, add a "Participant" filter, and select a specific participant
7. Verify only sessions for that participant are shown
8. Click "Session Details" link for a specific session

### View Session Details
1. From a session list, click "Session Details"
2. View session metadata: Participant, Remote ID, Status, Start/End times, Chatbot, Platform, Tags, Comments
3. Navigate between sessions using "Older" / "Newer" buttons
4. View tabs: Messages, Participant Data, Participant Schedules, Session State, Chatbot Events

### End a Session
1. From the session list, click "Session Details"
2. From the session detail page, click "End Session"

### Start New Session (from Session Detail)
1. From the session detail page, click "New Session"
2. Expect session status to be "awaiting final review"

### Generate Chat Export
1. From the chatbot's sessions tab, click "Generate Chat Export"
2. Expect report to be downloadable after a few seconds

---

## 9. Source Material

### Create Source Material
1. Navigate to "Source Material" in the sidebar
2. Click "Add new"
3. Fill in "Topic", description, and "Material" content
4. Click "Create"

### Search Source Material
1. Navigate to "Source Material" in the sidebar
2. Type a known topic name (e.g., "Test Source Material") in the search box and press Enter
3. Verify only the matching source material is shown in the table
4. Clear the search box, type a non-existent term (e.g., "nonexistent material") and press Enter
5. Verify the table shows "No source material found."

---

## 10. Surveys

### Create a Survey
1. Navigate to "Surveys" in the sidebar
2. Click "Add new"
3. Fill in "Name" and "Url" (external survey link)
4. Optionally modify the "User Message" template (uses `{survey_link}` placeholder)
5. Click "Create"

---

## 11. Consent Forms

### Create a Consent Form
1. Navigate to "Consent Forms" in the sidebar
2. Click "Add new"
3. Fill in "Name" and "Consent text"
4. Toggle "Capture identifier" (enabled by default)
5. Set "Identifier label" (default: "Email Address") and "Identifier type" (Email or Text)
6. Optionally modify "Confirmation text"
7. Click "Create"

---

## 12. Tags

### Create a Tag
1. Navigate to "Manage Tags" in the sidebar
2. Click "Add new"
3. Fill in "Name"
4. Click "Create"

---

## 13. Collections & Files

### Create a Media Collection
1. Navigate to "Collections" in the sidebar
2. Click "Add new"
3. Select "Media Collection" (share files with users)
4. Fill in "Name"
5. Click "Create"

### Create an Indexed Collection (RAG)
1. Navigate to "Collections" in the sidebar
2. Click "Add new"
3. Select "Indexed Collection (RAG)" (answer questions from document content)
4. Fill in "Name"
5. Click "Create"

---

## 14. Participants

### View Participants
1. Navigate to "Participants" in the sidebar
2. Use Filter and Date Range controls
3. Filter by chatbot using the dropdown

### Export Participants
1. Select a chatbot from the dropdown
2. Click "Export"

---

## 15. Prompt Builder

### Test a Prompt
1. Navigate to "Prompt Builder" in the sidebar
2. Fill in the "System" prompt field
3. Optionally fill in "Input Formatting"
4. Type a user message
5. Optionally click "Add message" to add more conversation turns
6. In the right panel, under "Prompt details": select source material, view token counts
7. Under "Model properties": select model provider and model
8. Click "Submit" to test the prompt
9. View the response

---

## 16. Profile & Account

### Update Profile
1. Navigate to "Profile" in the sidebar
2. Optionally click "Change Picture"
3. Update Email, First name, Last name
4. Click "Save"

### Configure Notification Preferences
1. Navigate to "Profile" in the sidebar
2. Under "Notification Preferences":
   - Toggle and set level for In-App Notifications (Info, Warning, Error)
   - Toggle and set level for Email Notifications (Info, Warning, Error)
3. Click "Save Preferences"

### Create an API Key
1. Navigate to "Profile"
2. Under "API Keys", click "New API Key"

---

## 17. Dashboard

### View Team Dashboard
1. Navigate to "Dashboard" in the sidebar
2. View analytics: Active Participants, Active Sessions, Message Volume Trends, Channel Breakdown, Average Response Time
3. View "Bot Performance Summary" table
4. View "Most Active Participants" and "Session Length Distribution"
5. Adjust filters: Date Range (7d/30d/3mo/1y/custom), Granularity (Hourly/Daily/Weekly/Monthly), Channels, Chatbots, Participants, Tags
6. Click "Save Filters" to persist selections

---

## 18. Notifications

### Trigger a Notification via Failed Chat
1. Navigate to the team page
2. Under "LLM and Embedding Model Service Providers", click "Add new"
3. Fill in "Name" (e.g., "Broken Provider"), select a provider type (e.g., OpenAI)
4. Fill in "API Key" with a nonsensical value (e.g., "invalid-key-12345")
5. Click "Create"
6. Navigate to "Chatbots" in the sidebar and create a new chatbot (or use an existing one)
7. Click "Edit" to open the pipeline editor
8. In the LLM node, select the broken provider and save the pipeline
9. Navigate back to the chatbot detail page (not the pipeline editor)
10. Click "Chat to the bot" to open the web chat tester
11. Send a message — the chat should fail due to the invalid API key
12. Navigate to "Notifications" in the sidebar
13. Verify a new error notification has been created related to the failed LLM call

### View Notifications
1. Navigate to "Notifications" in the sidebar
2. Use Filter and Date Range controls
3. Click "Silence" to mute notifications
4. Click "Preferences" to go to notification settings in profile
