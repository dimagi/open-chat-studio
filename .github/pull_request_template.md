<!--
NOTES
* Change to the chat widget should be kept separate from changes to the OCS code for the sake of the changelog and docs automation.
-->
### Product Description
<!--
A short summary of the change from a user perspective.
For non-user facing changes write 'no change'.
-->


### Technical Description
<!--
The primary goal of this section is to provide information to the reviewer to make it easier to review the PR.

Include technical details about the change and highlight the primary code changes and any decisions or design points
that the reviewer should be made aware of.

This should NOT be a summary of the every change. Focus on decisions and outcomes.
-->


### Migrations
<!--
There may be a potentially long window during the deployment where migrations are applied, but the old code is still running. We need to ensure that migrations can be applied to the current running code without breaking it, to the extent possible.

Delete this section if there are no migration.
 -->
- [ ] The migrations are backwards compatible


### Demo
<!--
If relevant, include screenshots or a loom video to demonstrate the new behaviour
**Include step-by-step instructions to enable functionality of the change
-->

### Docs and Changelog
- [ ] This PR requires docs/changelog update

<!--
Note: When this PR is merged and the checkbox above is checked, Claude will automatically analyze it and create a changelog entry in the docs repository.

Add any notes here that will help Claude write the changelog and docs.
-->

### Operator Impact
- [ ] Self-hosted operators must know about or act on this change

<!--
Check the box above if a self-hosted operator has to do something, or would be
surprised on upgrade: migrations, new/renamed/removed settings or env vars, a
change in deployment shape (process types, queues, backing-service versions),
a deprecation or removal, or a security fix needing operator action.

This is separate from the docs/changelog checkbox above, which covers the
user-facing product changelog. If checked, add an entry under `[Unreleased]` in
the repo-root `CHANGELOG.md` in this PR. See RELEASING.md.

Leave unchecked for a change that is purely a feature, improvement or bug fix
with none of the above — those reach operators through the user-facing
changelog. A feature PR that also carries a migration or a settings change is
still operator-impacting: check the box and log the migration.
-->
