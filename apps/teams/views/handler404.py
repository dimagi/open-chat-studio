from urllib.parse import quote

from django.http import (
    HttpResponseNotFound,
)
from django.template import Context, Engine, TemplateDoesNotExist, loader
from django.views.decorators.csrf import requires_csrf_token
from django.views.defaults import ERROR_PAGE_TEMPLATE


@requires_csrf_token
def page_not_found(request, exception, template_name="404.html"):
    """
    Default 404 handler.

    Templates: :template:`404.html`
    Context:
        request_path
            The path of the requested URL (e.g., '/app/pages/bad_page/'). It's
            quoted to prevent a content injection attack.
        exception
            The message from the exception which triggered the 404 (if one was
            supplied), or the exception class name
    """
    exception_repr = exception.__class__.__name__
    # Try to get an "interesting" exception message, if any (and not the ugly
    # Resolver404 dictionary)
    try:
        message = exception.args[0]
    except (AttributeError, IndexError):
        pass
    else:
        if isinstance(message, str):
            exception_repr = message
    context = {
        "request_path": quote(request.path),
        "exception": exception_repr,
    }
    try:
        template = loader.get_template(template_name)
        body = template.render(context, request)
    except TemplateDoesNotExist:
        if template_name != "404.html":
            # Reraise if it's a missing custom template.
            raise
        # Render template (even though there are no substitutions) to allow
        # inspecting the context in tests.
        template = Engine().from_string(
            ERROR_PAGE_TEMPLATE
            % {
                "title": "Not Found",
                "details": "The requested resource was not found on this server.",
            },
        )
        body = template.render(Context(context))
    return HttpResponseNotFound(body)
