# Feature Flags

Open Chat Studio uses [Django Waffle](https://waffle.readthedocs.io/) for feature flags with a custom team-based implementation. Feature flags allow you to toggle features on/off for specific teams without code deployments.

## Overview

Feature flags in Open Chat Studio are:
- **Team-scoped**: Flags can be enabled/disabled per team
- **Database-driven**: Stored in the database and configurable via a [custom admin page](../admin_guides/feature_flags.md)
- **Cached**: Uses Redis for performance
- **Convention-based**: New flags must follow naming conventions

## Custom Flag Model

The system uses a custom `Flag` model (`apps.teams.models.Flag`) that extends Django Waffle's `AbstractUserFlag` to support team-based activation:

```python
from apps.teams.models import Flag

# Create a flag
flag = Flag.objects.create(name="flag_new_feature")

# Activate for specific teams
flag.teams.add(my_team)

# Check if active for a team
flag.is_active_for_team(my_team)
```

!!! tip

    Flags are created automatically in the database when referenced in code or templates so it is not necessary to create them manually.

## Naming Convention

**All new feature flags MUST be prefixed with `flag_`**

```python
# ✅ Correct
"flag_new_dashboard"
"flag_enhanced_chat" 

# ❌ Incorrect - will raise ValidationError
"new_dashboard"
```

This naming convention:

- Prevents naming conflicts
- Makes flags easily identifiable in code
- Enables better tooling and management

## Usage in Code

### Python

In Django views or other Python code where a request object is available, you can check if a feature flag is active for the current team or user:

```python
from waffle import flag_is_active

def my_view(request):
    if flag_is_active(request, "flag_new_feature"):
        # New feature code
        return render(request, "new_template.html")
    else:
        # Legacy code
        return render(request, "old_template.html")
```

When a request object is not available, you can still check if a flag is active by using the `Flag` model directly:

```python
flag = Flag.get("flag_new_feature")
flag.is_active_for_team(team)
flag.is_active_for_user(user)
```

### Django Templates

```django
{% load waffle_tags %}

{% flag "flag_new_feature" %}
    <div class="new-feature">
        <!-- New feature UI -->
    </div>
{% endflag %}
```

## Management Commands

### Check Flag Usage

Use the `check_flag_usage` management command to find where flags are used in the codebase:

```bash
# Check all flags
python manage.py check_flag_usage

# Check specific flag
python manage.py check_flag_usage --flag-name flag_new_feature
```

**Output example:**
```
Found 5 flags in database

Flags found in code (3):
  ✓ flag_new_dashboard
    - apps/web/views.py
    - templates/dashboard.html
  ✓ flag_enhanced_chat
    - apps/chat/views.py

Flags not found in code (2):
  ✗ flag_old_feature
  ✗ flag_experimental_ui
```

This helps identify:

- **Active flags**: Currently used in code
- **Dead flags**: No longer referenced and can be removed

## Best Practices

### Naming
- Use descriptive names: `flag_enhanced_search` not `flag_search`
- Include the feature area: `flag_chat_reactions`, `flag_dashboard_v2`
- Avoid abbreviations: `flag_new_authentication` not `flag_new_auth`

### Code Organization
- Keep flag logic simple and readable
- Avoid deep nesting of feature flag conditions
- Consider extracting flag-dependent code into separate functions/classes

```python
# ✅ Good
def get_dashboard_data(request):
    if flag_is_active(request, 'flag_new_dashboard'):
        return get_enhanced_dashboard_data(request)
    return get_legacy_dashboard_data(request)

# ❌ Avoid deep nesting
def complex_view(request):
    if flag_is_active(request, 'flag_feature_a'):
        if flag_is_active(request, 'flag_feature_b'):
            # Deep nesting makes code hard to follow
```

### Documentation
- Document the purpose of each flag
- Include rollout plans in PR descriptions  
- Update team documentation when adding user-facing features

### Testing
- Test both flag states (enabled/disabled)
- Include flag state in test names

```python
def test_dashboard_with_new_feature_enabled(self):
    with override_flag('flag_new_dashboard', active=True):
        # Test new behavior

def test_dashboard_with_new_feature_disabled(self):
    with override_flag('flag_new_dashboard', active=False):
        # Test legacy behavior
```
