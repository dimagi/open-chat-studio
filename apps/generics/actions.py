import dataclasses
import uuid
from collections.abc import Callable
from typing import Any, Literal

from django.conf import settings
from django.template import Context
from django.template.loader import get_template
from django.urls import reverse
from django_tables2 import TemplateColumn


@dataclasses.dataclass
class Action:
    url_name: str
    url_factory: Callable[[str, Any, Any, Any], str] = None
    """A custom function called during rendering to generate the URL for the action. The function is passed
    the URL name, the request, the record, and the cell value and should return the URL."""

    label: str = None
    label_factory: Callable[[Any, Any], str] = None
    """A custom function called during rendering to generate the action label. The function is passed
    the row's record and cell's value and must return a string."""

    title: str = None
    icon_class: str = None
    button_style: str = None
    extra_context: dict = None
    required_permissions: list = dataclasses.field(default_factory=list)
    open_url_in_new_tab: bool = False
    display_condition: Callable[[Any, Any], bool] = None
    """A callable that takes a request and a record and returns a boolean indicating
    whether the action should be displayed."""

    enabled_condition: Callable[[Any, Any], bool] = None
    """A callable that takes a request and a record and returns a boolean indicating
    whether the action should be enabled. If none is provided, the action is always enabled."""

    template: str = "generic/action.html"

    def render(self, context: Context):
        request = context["request"]
        record = context.get("record")
        value = context.get("value")  # value from record that corresponds to the current column

        if not self.should_display(request, record):
            return ""

        template = get_template(self.template)
        with context.push(self.get_context(request, record, value)):
            return template.render(context.update(context.flatten()))

    def get_context(self, request, record, value):
        if self.url_factory:
            action_url = self.url_factory(self.url_name, request, record, value)
        else:
            args = [request.team.slug, record.pk] if record else [request.team.slug]
            action_url = reverse(self.url_name, args=args)

        label = self.label
        if not label and self.label_factory:
            label = self.label_factory(record, value)

        ctxt = {
            "action_url": action_url,
            "icon_class": self.icon_class,
            "label": label or "",
            "title": self.title or "",
            "disabled": not self.is_enabled(request, record),
            "open_url_in_new_tab": self.open_url_in_new_tab,
        }
        if self.button_style:
            ctxt["button_style"] = self.button_style
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

    def get_context(self, request, record, value):
        ctxt = super().get_context(request, record, value)
        ctxt.update(
            {"hx_method": self.hx_method, "confirm_message": self.confirm_message, "action_id": uuid.uuid4().hex}
        )
        return ctxt


@dataclasses.dataclass
class ModalAction(Action):
    """Action that will open a modal."""

    template: str = "generic/action_modal.html"
    modal_template: str = "generic/modal.html"
    modal_context: dict = None

    def get_context(self, request, record, value):
        ctxt = super().get_context(request, record, value)
        action_id = uuid.uuid4().hex
        modal_id = f"modal_{action_id}"
        ctxt.update(
            **{"action_id": action_id, "modal_id": modal_id, "modal_template": self.modal_template},
            **(self.modal_context or {}),
        )
        return ctxt


def edit_action(
    url_name: str,
    url_factory: Callable[[str, Any, Any, Any], str] = None,
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
    url_factory: Callable[[str, Any, Any, Any], str] = None,
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


def chip_action(
    label: str = None,
    label_factory: Callable[[Any, Any], str] = None,
    required_permissions: list = None,
    display_condition: callable = None,
    enabled_condition: callable = None,
    url_factory: Callable[[Any, Any, Any, Any], str] = None,
    icon_class: str = None,
    button_style: str = "",
    open_url_in_new_tab: bool = False,
):
    """Action to display a chip-style link that links to another page.

    This must be used with objects that implement the `get_absolute_url` method.

    Note: Keep the styling consistent with `generic/chip_button.html`"""
    if not label and not label_factory:

        def label_factory(record, value):
            return str(value)

    if url_factory is None:

        def url_factory(_, __, record, value):
            if hasattr(value, "get_absolute_url"):
                return value.get_absolute_url()
            return record.get_absolute_url()

    return Action(
        url_name="",
        url_factory=url_factory,
        label=label,
        label_factory=label_factory,
        icon_class=icon_class,
        button_style=button_style,
        required_permissions=required_permissions,
        display_condition=display_condition,
        enabled_condition=enabled_condition,
        open_url_in_new_tab=open_url_in_new_tab,
    )


class ActionsColumn(TemplateColumn):
    def __init__(
        self, actions, align: Literal["left", "right", "center"] = "center", extra_context: dict = None, **extra
    ):
        context = {"actions": actions}
        if extra_context:
            context.update(extra_context)
        if align != "left":
            th = settings.DJANGO_TABLES2_TABLE_ATTRS["th"].copy()
            th["class"] = th["class"].replace("text-left", f"text-{align}")
            td = settings.DJANGO_TABLES2_TABLE_ATTRS["td"].copy()
            td["class"] = td["class"].replace("text-left", f"text-{align}")
            extra = {"attrs": {"th": th, "td": td}, **extra}
        extra["orderable"] = False
        super().__init__(template_name="generic/crud_actions_column.html", extra_context=context, **extra)


def chip_column(label: str = None, align="left", **kwargs):
    """A column a chip link"""
    return ActionsColumn(actions=[chip_action(label=label)], align=align, **kwargs)
