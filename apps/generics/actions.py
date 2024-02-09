import dataclasses
import uuid

from django.template import Context
from django.template.loader import get_template


@dataclasses.dataclass
class Action:
    url_name: str
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
        ctxt = {
            "url_name": self.url_name,
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


def edit_action(url_name: str, required_permissions: list = None, display_condition: callable = None):
    return Action(
        url_name,
        icon_class="fa-solid fa-pencil",
        required_permissions=required_permissions,
        display_condition=display_condition,
    )


def delete_action(
    url_name: str,
    required_permissions: list = None,
    display_condition: callable = None,
    confirm_message: str = None,
):
    return AjaxAction(
        url_name,
        icon_class="fa-solid fa-trash",
        required_permissions=required_permissions,
        display_condition=display_condition,
        confirm_message=confirm_message,
        hx_method="delete",
    )
