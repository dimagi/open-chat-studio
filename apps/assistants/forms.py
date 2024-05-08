from django import forms

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.assistants.utils import get_assistant_tool_options, get_llm_providers_for_assistants
from apps.files.forms import get_file_formset


class OpenAiAssistantForm(forms.ModelForm):
    builtin_tools = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=get_assistant_tool_options())

    class Meta:
        model = OpenAiAssistant
        fields = ["name", "instructions", "builtin_tools", "llm_provider", "llm_model"]
        labels = {
            "builtin_tools": "Enable Built-in Tools",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["llm_provider"].queryset = get_llm_providers_for_assistants(request.team)
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProvider",
        }
        self.fields["llm_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"

        self.fields["builtin_tools"].widget.attrs = {
            "x-model.fill": "builtinTools",
        }

    def save(self, commit=True):
        self.instance.team = self.request.team
        return super().save(commit)


class ImportAssistantForm(forms.Form):
    assistant_id = forms.CharField(label="Assistant ID", max_length=255)
    llm_provider = forms.IntegerField()

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["llm_provider"].widget = forms.Select(
            choices=get_llm_providers_for_assistants(self.request.team).values_list("id", "name")
        )


class ToolResourceFileFormsets:
    def __init__(self, request):
        self.code_interpreter = get_file_formset(request, prefix="code_interpreter")
        self.file_search = get_file_formset(request, prefix="file_search")

    def is_valid(self):
        return self.code_interpreter.is_valid() and self.file_search.is_valid()

    def save(self, request, assistant):
        if "code_interpreter" in assistant.builtin_tools:
            self.create_tool_resources("code_interpreter", request, assistant, self.code_interpreter)
        if "file_search" in assistant.builtin_tools:
            self.create_tool_resources("file_search", request, assistant, self.file_search)

    def create_tool_resources(self, type_, request, assistant, form):
        files = form.save(request)
        if files:
            resources = ToolResources.objects.create(assistant=assistant, tool_type=type_)
            resources.files.set(files)
