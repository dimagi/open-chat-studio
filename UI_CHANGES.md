# UI/UX Changes for Deny List Support

## Experiment Form - Access Control Tab

### Before (Allowlist Only)
```
Tab: "Allowlist"
- Description text explaining allowlist
- Single multiselect for participant identifiers
- Empty list = public bot
- Non-empty list = restricted to listed participants
```

### After (Access Control with Three Options)
```
Tab: "Access Control"
- Radio button/Select dropdown for access level:
  • Open Access (Public) - Default
  • Allow List
  • Deny List

- Conditional sections based on selection:
  
  [When "Open Access" selected]
  - No additional fields shown
  - Bot is publicly accessible
  
  [When "Allow List" selected]
  - "Allowed Participants" multiselect appears
  - Description: "Only these participants (plus team members) can access the bot"
  - Existing allowlist functionality
  
  [When "Deny List" selected]
  - "Denied Participants" multiselect appears
  - Description: "These participants are blocked from accessing the bot. Everyone else (including team members) can access it"
  - NEW functionality
```

## Chatbot Settings View

### Before
```
Section: "Participant Allowlist"
- Read-only or edit mode
- Shows list of allowed participants or "No allowlist configured"
```

### After
```
Section: "Access Control"
- Shows current access level: Open Access (Public) / Allow List / Deny List
- When access level is "Allow List":
  Shows "Allowed Participants" list
- When access level is "Deny List":
  Shows "Denied Participants" list
- When access level is "Open":
  Shows "This chatbot is publicly accessible to everyone"
  
In edit mode:
- Dropdown to select access level
- Conditional multiselects based on selection (using Alpine.js x-show)
```

## Key UI Behaviors

### Reactive Controls
- When user changes access level, the appropriate list appears/disappears
- TomSelect multiselect components for easy tag-style input
- Support for creating new identifiers on-the-fly
- Copy to clipboard functionality for sharing lists

### Validation Feedback
- If user selects "Allow List" without adding participants: Error shown
- If user selects "Deny List" without adding participants: Error shown
- When switching modes, inappropriate list is automatically cleared

### Visual Indicators
- Current access mode clearly displayed
- Team members badge/indicator (always allowed)
- Empty state messages for each mode

## Example Workflows

### Workflow 1: Creating a Deny List Bot
1. Create/edit chatbot
2. Go to "Access Control" tab
3. Select "Deny List" from dropdown
4. Add blocked participant identifiers (emails, phone numbers)
5. Save - denylist is stored, allowlist is cleared

### Workflow 2: Switching from Allow to Deny List
1. Edit existing chatbot with allowlist
2. Go to "Access Control" tab
3. See current mode: "Allow List" with participants
4. Change to "Deny List"
5. Allowlist disappears, denylist field appears empty
6. Add denied participants
7. Save - allowlist cleared, denylist saved

### Workflow 3: Making Bot Public
1. Edit restricted chatbot
2. Go to "Access Control" tab
3. Select "Open Access (Public)"
4. Both lists disappear
5. Save - both lists cleared, bot is now public

## Accessibility Considerations

- Proper labels for all form fields
- Screen reader friendly descriptions
- Keyboard navigation support
- Clear error messages
- Visual feedback for state changes

## Mobile Responsiveness

- Stacked layout on mobile
- Touch-friendly multiselect controls
- Adequate spacing for tap targets
- Scrollable lists when needed
