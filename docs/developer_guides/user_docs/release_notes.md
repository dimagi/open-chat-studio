# Weekly Release Notes From Changelog Summaries

This is the automated process that turns the week's merged changelog entries into a single published release note.
A human developer must review them before they are published.

See the [User Docs overview](index.md) for how changelog entries get created in the first place.

## Human Review and publish

The process for manually reviewing and publishing a release is:

1. Review the repo diff between this release and the previous release using the 'Compare' dropdown in the left sidebar to ensure accuracy and completeness.
2. Review the previous release notes to see if there are any items that have already been included in a previous release.
3. If there are user docs to link to for any item, ensure that they are added.
4. If you think there should be user docs for the changes and there aren't, either create them immediately or open an [issue](https://github.com/dimagi/open-chat-studio-docs/issues) to be prioritized later.
5. Check that the release only contains these sections, and no top-level summary or upgrade recommendations:
    - New Features: new features added to the product
    - Improvements: changes to existing features that don't classify as 'new features'
    - Bug Fixes

Once you are happy with the release notes, publish the release. This will send a notification to all users who are subscribed to the docs GitHub repo release feed.

## How the release notes are generated

Once a week (currently on a Monday), a [GitHub Actions workflow](https://github.com/dimagi/open-chat-studio-docs/blob/main/.github/workflows/release.yml) runs and generates a [release note](https://github.com/dimagi/open-chat-studio-docs/releases) in the GitHub **docs repo** with a summary of the changes since the previous release, created in `draft` state so it can be reviewed before publishing.

This creates a way for OCS users to get notified of changes by subscribing to the release feed of the docs repo.
