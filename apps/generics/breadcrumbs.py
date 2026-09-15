"""Breadcrumb trail rendered by `generic/breadcrumbs.html` via the `breadcrumbs` context variable."""

Crumb = tuple[str, str | None]
"""A `(label, url)` pair. The final crumb is the current page and is never rendered as a link."""


class BreadcrumbsMixin:
    """Adds `get_breadcrumbs()` to the template context of a class-based view."""

    def get_breadcrumbs(self) -> list[Crumb]:
        raise NotImplementedError

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"breadcrumbs": self.get_breadcrumbs()}
