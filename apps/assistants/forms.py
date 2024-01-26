from django import forms

from apps.assistants.models import OpenAiAssistant


class OpenAiAssistantForm(forms.ModelForm):
    class Meta:
        model = OpenAiAssistant
        fields = ["name", "assistant_id", "instructions", "builtin_tools", "llm_provider", "llm_model"]

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["llm_provider"].queryset = request.team.llmprovider_set
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProvider",
        }
        self.fields["llm_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"

    def save(self, commit=True):
        self.instance.team = self.request.team
        return super().save(commit)
