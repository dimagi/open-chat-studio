import dataclasses

from django.template import Context
from django.template.loader import get_template


@dataclasses.dataclass
class Action:
    url_name: str
    icon_class: str = None
    extra_context: dict = None
    required_permissions: list = dataclasses.field(default_factory=list)
    display_condition: callable = None
    template: str = "generic/action.html"

    def render(self, context: Context):
        template = get_template(self.template)
        return template.render(self.get_context(context))

    def get_context(self, context: Context):
        ctxt = {
            "request": context["request"],
            "record": context["record"],
            "url_name": self.url_name,
            "icon_class": self.icon_class,
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


def edit_action(url_name: str, required_permissions: list = None, display_condition: callable = None):
    return Action(
        url_name,
        icon_class="fa-solid fa-pencil",
        required_permissions=required_permissions,
        display_condition=display_condition,
    )


def delete_action(url_name: str, required_permissions: list = None, display_condition: callable = None):
    return Action(
        url_name,
        icon_class="fa-solid fa-trash",
        required_permissions=required_permissions,
        display_condition=display_condition,
    )
