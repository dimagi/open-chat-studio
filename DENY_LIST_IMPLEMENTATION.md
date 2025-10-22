# Deny List Support Implementation Summary

## Overview
This implementation adds support for deny lists to complement the existing allow lists for participant access control in Open Chat Studio chatbots.

## Changes Made

### 1. Database Changes
- **New Field**: `participant_access_level` - CharField with choices: OPEN, ALLOW_LIST, DENY_LIST
- **New Field**: `participant_denylist` - ArrayField to store denied participant identifiers
- **Migration**: `0118_add_participant_denylist_support.py` includes data migration to set access_level based on existing allowlists

### 2. Model Changes (`apps/experiments/models.py`)
- Added `ParticipantAccessLevel` enum import
- Updated `is_public` property to check `participant_access_level == OPEN`
- Updated `is_participant_allowed` method with comprehensive logic:
  - Team members are always allowed
  - OPEN: Everyone allowed
  - ALLOW_LIST: Only those in allowlist allowed
  - DENY_LIST: Everyone except those in denylist allowed
- Added fields to version tracking

### 3. Form Changes
- **`apps/experiments/forms.py`**: 
  - Added `participant_access_level`, `participant_denylist` to fields
  - Added validation to ensure lists match access level
  - Clear inappropriate list based on access level
  
- **`apps/chatbots/forms.py`**:
  - Similar changes to support chatbot settings

### 4. UI Changes
- **`templates/experiments/experiment_form.html`**:
  - Renamed "Allowlist" tab to "Access Control"
  - Added radio buttons for access level selection
  - Added conditional display of allowlist/denylist multiselect based on access level
  - Updated JavaScript to handle both multiselects

- **`templates/chatbots/settings_content.html`**:
  - Renamed section to "Access Control"
  - Added Alpine.js reactive UI for access level selection
  - Added display logic for both lists

### 5. View Changes
- Updated `apps/experiments/views/experiment.py` to include denylist in participant identifiers
- Updated `apps/chatbots/views.py` similarly

### 6. Test Coverage
- **`apps/experiments/tests/test_access_control.py`**: Comprehensive model-level tests
- **`apps/channels/tests/test_channel_denylist.py`**: Channel behavior tests
- **`apps/api/tests/test_access_control.py`**: API access control tests
- Updated existing API tests to explicitly set access_level

## Backward Compatibility

The implementation maintains full backward compatibility:
- Existing experiments with empty allowlists will have `access_level = OPEN` after migration
- Existing experiments with allowlists will have `access_level = ALLOW_LIST` after migration
- All existing behavior is preserved

## Access Control Logic

### OPEN Access
- `is_public = True`
- Anyone can access the chatbot
- Both allowlist and denylist are ignored/cleared

### ALLOW_LIST Access
- `is_public = False`
- Only participants in `participant_allowlist` can access
- Team members can always access
- Denylist is cleared

### DENY_LIST Access
- `is_public = False`
- Everyone EXCEPT those in `participant_denylist` can access
- Team members can always access (even if in denylist)
- Allowlist is cleared

## Form Validation

Forms enforce the following rules:
- When access_level is OPEN: Both lists are cleared
- When access_level is ALLOW_LIST: Denylist is cleared, allowlist must not be empty
- When access_level is DENY_LIST: Allowlist is cleared, denylist must not be empty

## Testing Instructions

### Manual Testing
1. Create/Edit an experiment
2. Navigate to "Access Control" tab
3. Test each access level:
   - **Open**: Verify bot is accessible to anyone
   - **Allow List**: Add identifiers, verify only those can access
   - **Deny List**: Add identifiers, verify those are blocked but others can access
4. Test team member access always works
5. Test switching between access levels

### Automated Testing
Run the test suite:
```bash
pytest apps/experiments/tests/test_access_control.py -v
pytest apps/channels/tests/test_channel_denylist.py -v
pytest apps/api/tests/test_access_control.py -v
```

## Future Considerations

1. **UI Enhancement**: Consider adding bulk import/export for deny lists
2. **Audit Logging**: Track when participants are added/removed from lists
3. **Analytics**: Track denied access attempts
4. **Rate Limiting**: Consider rate limiting for denied participants
