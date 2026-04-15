# Contributing to User Documentation

**OCS User** documentation is maintained in a separate GitHub repository [open-chat-studio-docs](https://github.com/dimagi/open-chat-studio-docs) and published at [docs.openchatstudio.com](https://docs.openchatstudio.com/).

Please provide feedback on your experience with the user guides. If you notice errors or opportunities for improvement, reach out to a documentation contributor or open a Pull Request.

## Keeping Code Links in Sync

When pages are moved or renamed in the user documentation, the corresponding links in `config/settings.py` must also be updated. See: [`DOCUMENTATION_LINKS` in `config/settings.py`](https://github.com/dimagi/open-chat-studio/blob/main/config/settings.py#L694-L717)


The `DOCUMENTATION_LINKS` dictionary in `config/settings.py` maps descriptive keys to documentation URLs, centralizing help content references across the application.

This dictionary is used by various UI components including templates, pipeline nodes, forms, and frontend JavaScript.

For example:
- Show “Learn more” links in forms/help text
- Attach docs URLs to each of the different pipeline node types
- Provide documentation for feature flags
- Expose docs links to Django templates

## Change Log in User Docs

See [Changelog Process](../developer_guides/user_docs.md)
