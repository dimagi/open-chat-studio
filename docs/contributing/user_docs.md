# Contributing to User Documentation

If you use Open Chat Studio and notice errors or opportunities for improvement in the user documentation, you are welcome to reach out to a documentation contributor or make small improvements directly in the user docs repository.

The user documentation is maintained in the separate repository [open-chat-studio-docs](https://github.com/dimagi/open-chat-studio-docs) and published at [docs.openchatstudio.com](https://docs.openchatstudio.com/).

This guide explains how to make user docs changes that are clear, accurate, and helpful for end users.

## Before You Make a Change

- Is this a user docs change, or should it go in [developer docs instead](#user-docs-vs-developer-docs)?
- If you have user-facing code changes, use the AI-assisted docs/changelog automation [(i.e., the changelog process)](../developer_guides/user_docs.md#manual-trigger-in-docs-repo).
- Are you updating an existing page, or should this be a new page?
- Does the wording match the current product UI?
- Will renaming or removing a page [break links](#if-you-move-or-rename-a-docs-page) pointing to that page?

## How to Make Good User Docs Changes

- Write for product users, not for developers.
- Use the labels and terminology that appear in the UI.
- If you add a new concept, link to related pages where helpful.

### Choose the Right Type of Page

User documentation works best when each page has a clear purpose.

1. **Tutorials**
   For helping a new user learn by doing.

2. **How-to guides**
   For helping a user complete a specific task.

3. **Concepts**
   For explaining how something works and why it matters.

4. **Tech Hub**
   A reference for precise factual information such as advanced settings, example code or calling APIs.

Avoid mixing these modes on one page. If a page tries to teach, explain, and act as reference at the same time, it becomes harder to use.

## If You Move or Rename a Docs Page
Some parts of the OCS application link directly to user documentation pages, including help text, pipeline node docs, and feature flag guidance.

If you are only editing page content, this usually does not apply.

### Steps
1. If you move or rename a page in the docs repository, the corresponding link in the main OCS codebase may also need to be updated in  [`DOCUMENTATION_LINKS` in `config/settings.py`](https://github.com/dimagi/open-chat-studio/blob/main/config/settings.py).
2. If you are working from a fork of the main OCS codebase, then run `python manage.py update_pipeline_schema` to manually update the pipeline schema files that use these `DOCUMENTATION_LINKS`.


## User Docs vs Developer Docs

- Update the user docs repo if the content is for product users.
- Update the `docs/` directory in this repository if the content is for developers maintaining or extending OCS.
