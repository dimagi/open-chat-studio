from django import forms

from apps.assistants.models import OpenAiAssistant
from apps.assistants.utils import get_assistant_tool_options, get_llm_providers_for_assistants


class OpenAiAssistantForm(forms.ModelForm):
    class Meta:
        model = OpenAiAssistant
        fields = ["name", "instructions", "builtin_tools", "llm_provider", "llm_model"]
        labels = {
            "builtin_tools": "Enable Built-in Tools",
        }
        widgets = {
            "builtin_tools": forms.CheckboxSelectMultiple(choices=get_assistant_tool_options()),
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["llm_provider"].queryset = get_llm_providers_for_assistants(request.team)
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProvider",
        }
        self.fields["llm_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"

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
