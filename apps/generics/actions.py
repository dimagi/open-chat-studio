import dataclasses
import uuid
from collections.abc import Callable
from typing import Any

from django.template import Context
from django.template.loader import get_template
from django.urls import reverse


@dataclasses.dataclass
class Action:
    url_name: str
    url_factory: Callable[[str, Any, Any], str] = None
    """A custom function called during rendering to generate the URL for the action. The function is passed
    the URL name, the request, and the record, and should return the URL."""

    label: str = None
    title: str = None
    icon_class: str = None
    extra_context: dict = None
    required_permissions: list = dataclasses.field(default_factory=list)
    display_condition: callable = None
    """A callable that takes a request and a record and returns a boolean indicating
    whether the action should be displayed."""

    enabled_condition: callable = None
    """A callable that takes a request and a record and returns a boolean indicating
    whether the action should be enabled. If none is provided, the action is always enabled."""

    template: str = "generic/action.html"

    def render(self, context: Context):
        request = context["request"]
        record = context.get("record")

        if not self.should_display(request, record):
            return ""

        template = get_template(self.template)
        with context.push(self.get_context(request, record)):
            return template.render(context.update(context.flatten()))

    def get_context(self, request, record):
        if self.url_factory:
            action_url = self.url_factory(self.url_name, request, record)
        else:
            args = [request.team.slug, record.pk] if record else [request.team.slug]
            action_url = reverse(self.url_name, args=args)
        ctxt = {
            "action_url": action_url,
            "icon_class": self.icon_class,
            "label": self.label or "",
            "title": self.title or "",
            "disabled": not self.is_enabled(request, record),
        }
        if self.extra_context:
            ctxt.update(self.extra_context)
        return ctxt

    def should_display(self, request, record):
        if self.required_permissions and not request.user.has_perms(self.required_permissions):
            return False
        if self.display_condition:
            return self.display_condition(request, record)
        return True

    def is_enabled(self, request, record):
        if self.enabled_condition:
            return self.enabled_condition(request, record)
        return True


@dataclasses.dataclass
class AjaxAction(Action):
    template: str = "generic/action_ajax.html"

    hx_method: str = "post"
    """The HTTP method to use when making the request. One of 'get', 'post', 'put', 'patch', or 'delete'."""

    confirm_message: str = None
    """A message to display in a confirmation dialog when the action is clicked.
    If none is provided, no confirmation dialog is shown."""

    def get_context(self, request, record):
        ctxt = super().get_context(request, record)
        ctxt.update(
            {"hx_method": self.hx_method, "confirm_message": self.confirm_message, "action_id": uuid.uuid4().hex}
        )
        return ctxt


def edit_action(
    url_name: str,
    url_factory: Callable[[str, Any, Any], str] = None,
    required_permissions: list = None,
    display_condition: callable = None,
    template: str | None = None,
):
    kwargs = {}
    if template:
        kwargs["template"] = template
    return Action(
        url_name=url_name,
        url_factory=url_factory,
        icon_class="fa-solid fa-pencil",
        required_permissions=required_permissions,
        display_condition=display_condition,
        **kwargs,
    )


def delete_action(
    url_name: str,
    url_factory: Callable[[str, Any, Any], str] = None,
    required_permissions: list = None,
    display_condition: callable = None,
    confirm_message: str = None,
    template: str | None = None,
):
    kwargs = {}
    if template:
        kwargs["template"] = template
    return AjaxAction(
        url_name,
        url_factory=url_factory,
        icon_class="fa-solid fa-trash",
        required_permissions=required_permissions,
        display_condition=display_condition,
        confirm_message=confirm_message,
        hx_method="delete",
        **kwargs,
    )
