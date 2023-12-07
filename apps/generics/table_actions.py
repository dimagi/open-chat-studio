from django.template import Context, Template
from django.template.loader import get_template


class BaseAction:
    template = None

    def __init__(self, url_name: str, extra_context: dict = None, required_permissions: list = None):
        self.url_name = url_name
        self.extra_context = extra_context
        self.required_permissions = required_permissions or []

    def render(self, context: Context):
        template = get_template(self.template)
        return template.render(self.get_context(context))

    def get_context(self, context: Context):
        ctxt = {"request": context["request"], "record": context["record"], "url_name": self.url_name}
        if self.extra_context:
            ctxt.update(self.extra_context)
        return ctxt


class Action(BaseAction):
    template = "generic/action.html"

    def __init__(self, url_name: str, icon_class: str, required_permissions: list = None):
        super().__init__(url_name, extra_context={"icon_class": icon_class}, required_permissions=required_permissions)


class EditAction(Action):
    def __init__(self, url_name: str, required_permissions: list = None):
        super().__init__(url_name, "fa-solid fa-pencil", required_permissions=required_permissions)


class DeleteAction(BaseAction):
    template = "generic/action_delete.html"
