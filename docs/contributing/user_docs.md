# Contributing to User Documentation

OCS user documentation is maintained in a separate GitHub repository [open-chat-studio-docs](https://github.com/dimagi/open-chat-studio-docs) and published at [docs.openchatstudio.com](https://docs.openchatstudio.com/).

Please provide feedback on your experience with the user guides. If you notice errors or opportunities for improvement, reach out to a documentation contributor or open a Pull Request.

## Keeping Code Links in Sync

When pages are moved or renamed in the user documentation, the corresponding links in `config/settings.py` must also be updated. See: [`DOCUMENTATION_LINKS` in `config/settings.py`](https://github.com/dimagi/open-chat-studio/blob/main/config/settings.py#L694-L717)

The `DOCUMENTATION_LINKS` dictionary in `config/settings.py` maps descriptive keys to documentation URLs, centralizing help content references across the application.

This dictionary is used by various UI components including templates, pipeline nodes, forms, and frontend JavaScript.

For example, it is used to:
- Show "Learn more" links in forms and help text
- Attach documentation URLs to each pipeline node type
- Provide documentation links for feature flags
- Expose documentation links to Django templates

## Changelog in User Docs

The User Documentation contains an up-to-date [Changelog](https://docs.openchatstudio.com/changelog/) page.

See the [Changelog Process](../developer_guides/user_docs.md) for details on how user-facing changes are automatically reflected in the changelog and how weekly release notes are published.
