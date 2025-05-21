# Changelog process

The changelog is hosted in the [doc repo][docs_repo] and published to https://docs.openchatstudio.com/changelog/.

In principle, all user-facing changes should be accompanied by a changelog but discretion should be used. For example, if a change is purely internal and doesn't affect the user experience, it may not need to be included or if it is a very minor change.

Ideally, when creating a PR, also create a PR in the [docs repo][docs_repo] with any documentation updates and a changelog entry and then add a link to the docs PR in your code changes PR.

Changelog entries should be brief but should link to any relevant documentation for further details.

## Changelog summaries

Once a week (currently on a Monday) a GitHub actions workflow runs and generates a [release](https://github.com/dimagi/open-chat-studio-docs/releases) in the docs repo with a summary of the changes since the previous release.
This creates a way for users to get notified of changes by subscribing to the release feed.

The automated releases are created in `draft` state which allows a developer to review the generated text before publishing. The releases should contain the following sections:

* New Features: new features added to the prodcut
* Improvements: changes to existing features that don't classify as 'new features'
* Bug Fixes

It should not contain a top level summary, upgrade recommendations, etc.

The process for manually reviewing and publishing a release is:

1. Review the repo diff between this release and the previous release. You can access this by using the 'Compare' drop down in the left sidebar. This is a good idea to ensure that the release notes are accurate and complete.
2. Review the previous release notes to see if there are any items that have already been included in a previous release.
3. If there are docs to link to for any item, ensure that they are added.
4. If you think there should be docs where there aren't, either create them immediately or create a ticket to be prioritized later.

Once you are happy with the release notes, publish the release. This will send a notification to all users who are subscribed to the release feed.

[docs_repo]: https://github.com/dimagi/open-chat-studio-docs
