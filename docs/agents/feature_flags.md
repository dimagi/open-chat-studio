# Feature Flags

OCS uses [django-waffle](https://waffle.readthedocs.io/) for feature flags, with a custom registry layer in `apps/teams/flags.py`.

## Defining a flag

All flags must be declared in `apps/teams/flags.py` as entries in the `Flags` enum:

```python
class Flags(FlagInfo, Enum):
    MY_FEATURE = (
        "flag_my_feature",   # slug — must start with "flag_"
        "Short description", # shown in the admin UI
        "docs-slug",         # key into settings.DOCUMENTATION_LINKS (optional, "" if none)
        [],                  # list of other flag slugs this flag requires (optional)
        True,                # teams_can_manage: team admins can toggle this themselves (optional)
    )
```

`FlagInfo` fields:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `slug` | `str` | required | Waffle flag name, used everywhere |
| `description` | `str` | required | Human-readable label shown in admin |
| `docs_slug` | `str` | `""` | Key into `settings.DOCUMENTATION_LINKS` for a docs link |
| `requires` | `list[str]` | `[]` | Other flag slugs auto-enabled alongside this one |
| `teams_can_manage` | `bool` | `False` | Whether team admins can toggle this via Settings UI |
| `removed` | `bool` | `False` | Marks a fully-removed flag (see Removal below) |

Flags are created in the database lazily (on first use) via `Flag.objects.get_or_create`. No migration is needed when adding a flag.

## Using a flag

### Python

```python
from waffle import flag_is_active
from apps.teams.flags import Flags

# Preferred: reference slug via the enum to avoid typos
if flag_is_active(request, Flags.MY_FEATURE.slug):
    ...

# Also acceptable for one-off checks
if flag_is_active(request, "flag_my_feature"):
    ...
```

### Django templates

```html
{% load waffle_tags %}

{% flag "flag_my_feature" %}
  {# content shown only when flag is active #}
{% endflag %}
```

### Pipeline node fields (UI gating)

For pipeline node fields that should only appear when a flag is active, use `flag_required`:

```python
my_field: str = Field(..., flag_required="flag_my_feature")
```

## Testing

Use `waffle.testutils.override_flag` as a context manager or decorator:

```python
from waffle.testutils import override_flag

def test_my_feature(client):
    with override_flag("flag_my_feature", active=True):
        response = client.get(url)
    assert response.status_code == 200
```

When a flag is removed (rolled out to everyone), remove the `override_flag` call and test the behaviour unconditionally.

## Auditing flag usage

A management command scans the codebase for all flag references:

```bash
uv run python manage.py check_flag_usage
uv run python manage.py check_flag_usage --flag-name flag_my_feature
```

This is useful before removing a flag to find every place it is referenced.

## Removing a flag (rolling out to everyone)

When a feature is stable and should be on for all users, remove the flag entirely rather than leaving dead code. Steps:

1. Run `check_flag_usage` to find every reference.
2. Remove all `flag_is_active` / `{% flag %}` / `override_flag` guards, keeping the guarded code.
3. Delete the `Flags` enum entry from `apps/teams/flags.py`.
4. Update or remove tests that used `override_flag` for this flag.
5. The flag row in the database will become orphaned and can be deleted via the Django admin.

Do **not** set `removed = True` and leave the entry in the enum unless the flag needs to be kept as a tombstone for documentation purposes. Prefer full deletion.
