# Feature Flags

Feature flags let you turn features on or off without deploying code. For implementation details, see the [developer guide](../developer_guides/feature_flags.md).

## Where feature flags are managed

There are two places to manage feature flags:

1. **Global Admin > Feature Flags** (`/admin/flags/`)
   - Intended for platform-wide management of all flags.
   - The left navigation shows this menu item to staff and superusers.
   - Access to the page itself is restricted to superusers.
2. **Team Settings > Manage Feature Flags**
   - Intended for team-specific flag management.
   - Only flags marked as team-manageable are shown.
   - Team members can view this page, but only team admins can save changes.

## How a flag becomes active

A flag is active when one or more configured conditions match. You can activate a flag for:

- Everyone (all users)
- Superusers
- Specific teams
- Specific users

In addition, Django Waffle supports these modes:

- [Testing mode](https://waffle.readthedocs.io/en/stable/testing/user.html#testing-user): activate a flag with a URL parameter.
- [Rollout mode](https://waffle.readthedocs.io/en/stable/types/flag.html#rollout-mode): activate a flag for a percentage of users.
