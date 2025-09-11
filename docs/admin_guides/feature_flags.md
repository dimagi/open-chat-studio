# Feature Flags

Feature flags allow you to toggle features on/off for specific teams without code deployments. For technical details on how flags are created and used in code see the [developer guide](../developer_guides/feature_flags.md).

Access to feature flags is managed via a custom page in OCS which is only accessible to users with the 'staff' or 'superuser' permission. Users with those permissions will see a 'Feature Flags' menu item at the bottom of the left side menu.

Feature flags can be activated for:

* Everyone (all users)
* Superusers
* Specific teams
* Specific users

If any of these matches for a user, they will have access to that feature flag.

There are also two special modes:

* [Testing mode](https://waffle.readthedocs.io/en/stable/testing/user.html#testing-user)
  * This allows activating a flag using a URL parameter 
* [Rollout mode](https://waffle.readthedocs.io/en/stable/types/flag.html#rollout-mode)
  * This allows activating a flag for a percentage of users. 
