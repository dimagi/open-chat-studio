from django import forms

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.assistants.utils import get_assistant_tool_options, get_llm_providers_for_assistants
from apps.experiments.models import AgentTools
from apps.files.forms import get_file_formset
from apps.utils.prompt import validate_prompt_variables

INSTRUCTIONS_HELP_TEXT = """
    <div class="tooltip" data-tip="
        Available variables to include in your prompt: {participant_data} and
        {current_datetime}.
        {participant_data} is optional.
        {current_datetime} is only required when the bot is using a tool.
    ">
        <i class="text-xs fa fa-circle-question">
        </i>
    </div>
"""


class OpenAiAssistantForm(forms.ModelForm):
    builtin_tools = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=get_assistant_tool_options())
    tools = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=AgentTools.choices, required=False)
    custom_actions = forms.ModelMultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=False, queryset=None)

    class Meta:
        model = OpenAiAssistant
        fields = [
            "name",
            "instructions",
            "include_file_info",
            "builtin_tools",
            "tools",
            "llm_provider",
            "llm_model",
            "temperature",
            "top_p",
            "custom_actions",
        ]
        labels = {
            "builtin_tools": "Enable Built-in Tools",
        }
        help_texts = {"instructions": INSTRUCTIONS_HELP_TEXT}

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["llm_provider"].queryset = get_llm_providers_for_assistants(request.team)
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProvider",
        }
        self.fields["llm_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"
        self.fields["include_file_info"].help_text = """If checked, extra information about uploaded files will
            be appended to the instructions. This will give the assistant knowledge about the file types."""
        self.fields["builtin_tools"].required = False
        self.fields["builtin_tools"].widget.attrs = {
            "x-model.fill": "builtinTools",
        }
        self.fields["custom_actions"].queryset = request.team.customaction_set.all()

    def clean(self):
        cleaned_data = super().clean()
        validate_prompt_variables(
            form_data=cleaned_data,
            prompt_key="instructions",
            known_vars={"participant_data", "current_datetime"},
        )
        return cleaned_data

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
