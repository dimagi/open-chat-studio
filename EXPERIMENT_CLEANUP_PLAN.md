# Experiment Tab Cleanup Plan

## Context
The team has completed the migration from Experiments to Chatbots. This is a comprehensive plan to remove all experiment-tab specific code while preserving shared infrastructure.

## Summary
- **Estimated removals:** ~1000+ lines
- **Views to remove:** ~15 functions
- **Templates to remove:** ~30 files  
- **URL patterns to remove:** ~20
- **Test functions to remove:** ~10
- **Middleware to remove:** 1 class

---

## 1. DUPLICATE VIEWS TO REMOVE

These views in `apps/experiments/views/experiment.py` have equivalents in `apps/chatbots/views.py`:

### Session Views
- `start_authed_web_session` (lines 264-275) → Duplicate exists in both files, keep chatbots version
- `experiment_chat_session` (lines 278-304) → Use `chatbot_chat_session` instead
- `experiment_session_details_view` (lines 1096-1106) → Use `chatbot_session_details_view`
- `experiment_session_pagination_view` (lines 1109-1117) → Use `chatbot_session_pagination_view`

### Chat Views
- `experiment_chat` (lines 756-759) → Use `chatbot_chat`
- `experiment_chat_embed` (lines 762-773) → Use `chatbot_chat_embed`
- `_experiment_chat_ui` (lines 776-794) → Use `_chatbot_chat_ui`

### Invitation Views
- `send_invitation` (lines 676-683) → Functionality moved to `chatbot_invitations`

---

## 2. EXPERIMENT-SPECIFIC VIEWS TO REMOVE

These are experiment-only flows that chatbots doesn't need:

### Pre/Post Survey & Review
- `experiment_pre_survey` (lines 725-754)
- `experiment_review` (lines 1063-1093)
- `experiment_complete` (lines 1086-1097)

### Consent & Invites
- `start_session_from_invite` (lines 685-722)
- `verify_public_chat_token` (lines 647-658)

### End Experiment
- `end_experiment` (lines 1054-1061) → Chatbots has `end_chatbot_session`

---

## 3. TEMPLATES TO REMOVE

### Experiment-specific pages (~6+ core templates)
- `templates/experiments/experiment_complete.html`
- `templates/experiments/experiment_list.html`
- `templates/experiments/experiment_review.html`
- `templates/experiments/experiment_session_view.html`
- `templates/experiments/pre_survey.html`
- `templates/experiments/start_experiment_session.html`

### Email templates
- `templates/experiments/email/invitation.html`
- `templates/experiments/email/verify_public_chat_email.html`

### Components & partials
- `templates/experiments/manage/invite_row.html`
- `templates/experiments/chat/end_experiment_modal.html`
- `templates/experiments/chat/experiment_response_htmx.html`
- `templates/experiments/components/experiment_actions_column.html`
- `templates/experiments/components/experiment_session_messages_control_panel.html`
- `templates/experiments/components/experiment_version_actions.html`
- `templates/experiments/components/experiment_version_cell.html`

**Note:** Some chat templates may be shared between experiments and chatbots. Need to verify usage before deleting.

### Templates to KEEP (used by chatbots)
- `experiments/chat/web_chat.html` (shared)
- `experiments/components/experiment_version_details_content.html`
- `experiments/create_version_button.html`
- `experiments/create_version_form.html`
- `experiments/experiment_version_table.html`
- `experiments/settings_content.html`

---

## 4. URL PATTERNS TO REMOVE

From `apps/experiments/urls.py`:

### Duplicate session URLs (lines 66-100)
```python
# These have chatbots equivalents - REMOVE:
path("e/<int:experiment_id>/v/<int:version_number>/start_authed_web_session/")
path("e/<int:experiment_id>/v/<int:version_number>/session/<int:session_id>/")
path("e/<uuid:experiment_id>/v/<int:version_number>/session/<str:session_id>/message/")
path("e/<uuid:experiment_id>/v/<int:version_number>/session/<str:session_id>/embed/message/")
path("e/<uuid:experiment_id>/session/<str:session_id>/get_response/<slug:task_id>/")
path("e/<uuid:experiment_id>/session/<str:session_id>/poll_messages/")
path("e/<uuid:experiment_id>/session/<str:session_id>/poll_messages/embed/")
```

### Experiment-specific workflows (lines 110-140)
```python
# Experiment-only flows - REMOVE:
path("e/<uuid:experiment_id>/s/<str:session_id>/")              # start_session_from_invite
path("e/<uuid:experiment_id>/s/<str:session_id>/pre-survey/")   # pre_survey
path("e/<uuid:experiment_id>/s/<str:session_id>/chat/")         # chat
path("e/<uuid:experiment_id>/s/<str:session_id>/embed/chat/")   # embed chat
path("e/<uuid:experiment_id>/s/<str:session_id>/end/")          # end_experiment
path("e/<uuid:experiment_id>/s/<str:session_id>/review/")       # review
path("e/<uuid:experiment_id>/s/<str:session_id>/complete/")     # complete
path("e/<uuid:experiment_id>/s/<str:session_id>/view/")         # details
path("e/<uuid:experiment_id>/s/<str:session_id>/paginate/")     # pagination
```

### URLs to KEEP (shared or still used)
- `start_session_public` - imported by chatbots
- `start_session_public_embed` - imported by chatbots
- `experiment_session_messages_view` - message viewing
- `translate_messages_view` - translation feature
- Export URLs - still used
- File download URLs - still used

---

## 5. TESTS TO REMOVE

From `apps/experiments/tests/test_views.py`:

### Tests for removed views
- `test_start_authed_web_session_with_version`
- Tests in `TestPublicSessions` class (consent flows)
- Tests in `TestVerifyPublicChatToken` (token verification)

### Tests to KEEP
- Shared model tests
- Filter tests
- Export tests
- Core experiment functionality used by chatbots

---

## 6. MIDDLEWARE TO REMOVE

`apps/generics/middleware.py`:
- **Remove:** `OriginDetectionMiddleware` class
  - Comment says "This is a temporary middleware to aid in the migration"
  - Migration is complete
- **Update:** Remove from `MIDDLEWARE` in settings

---

## 7. CONDITIONAL LOGIC TO CLEAN UP

### Template conditionals
```html
<!-- Find and remove/simplify: -->
{% if active_tab == "experiments" %}
```

### View conditionals
```python
# Replace this pattern:
active_tab = "chatbots" if request.origin == "chatbots" else "experiments"

# With this:
active_tab = "chatbots"
```

### Files with `request.origin` checks
- `apps/events/tables.py:18` - remove experiments fallback
- `apps/experiments/tables.py:159` - change origin to "chatbots"
- `apps/experiments/views/experiment.py:190, 576, 800` - remove conditionals

---

## 8. JAVASCRIPT REVIEW

`assets/javascript/dashboard/main.js`:
- Most "experiments" references are for model field names (OK to keep)
- Review and update comments if needed
- No major changes expected

---

## 9. WHAT TO KEEP

### Models & Migrations
- **DO NOT REMOVE** any models
- **DO NOT REMOVE** any migrations
- Experiment model is core infrastructure

### Django Admin
- `apps/experiments/admin.py` - Keep (for Django admin interface)

### Shared Utilities
- Any utilities used by chatbots
- Core experiment logic
- Export functionality

---

## Implementation Plan

### Phase 1: Remove Duplicate Views ✓
1. Delete duplicate view functions from `apps/experiments/views/experiment.py`
2. Remove corresponding URLs from `apps/experiments/urls.py`
3. Update any imports

### Phase 2: Remove Experiment-Specific Views ✓
1. Delete experiment-only views
2. Remove their URLs
3. Remove their templates

### Phase 3: Clean Up Tests ✓
1. Remove tests for deleted views
2. Update remaining tests

### Phase 4: Remove Middleware ✓
1. Delete `OriginDetectionMiddleware`
2. Remove from settings

### Phase 5: Clean Up Conditionals ✓
1. Replace `request.origin` checks
2. Remove `active_tab="experiments"` conditionals

### Phase 6: Remove Unused Templates ✓
1. Verify template usage
2. Delete unused templates

---

## Risk Assessment

### ✅ Low Risk (Safe to remove)
- Duplicate views (chatbots has equivalents)
- Experiment-specific workflows
- Middleware (marked as temporary)

### ⚠️ Medium Risk (Verify first)
- Templates (some may be shared)
- URLs (check for external links)
- JavaScript references

### 🚫 High Risk (DO NOT remove)
- Models
- Migrations  
- Shared utilities
- Core experiment logic used by chatbots

---

## Verification Checklist

After cleanup, verify:
- [ ] All chatbots functionality works
- [ ] No broken imports or references
- [ ] All tests pass
- [ ] No 404s on chatbot pages
- [ ] Settings/versions/exports still work
- [ ] Code is cleaner and more maintainable

---

## Success Criteria

1. ✅ Chatbots tab fully functional
2. ✅ No experiment-tab specific code remains
3. ✅ All tests passing
4. ✅ No dead imports
5. ✅ ~1000+ lines of code removed

---

*This cleanup removes ALL experiment-tab specific code while carefully preserving the shared infrastructure that chatbots relies on.*
