from django.template import Context, Template
from django.template.loader import get_template


class Action:
    def __init__(self, template: str, url_name: str):
        self.template = template
        self.url_name = url_name

    def render(self, context: Context):
        template = get_template(self.template)
        return template.render({"request": context["request"], "record": context["record"], "url_name": self.url_name})


class DeleteAction(Action):
    def __init__(self, url_name: str):
        super().__init__("generic/action_delete.html", url_name)


class EditAction(Action):
    def __init__(self, url_name: str):
        super().__init__("generic/action_edit.html", url_name)
