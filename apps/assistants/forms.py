from django import forms

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.assistants.utils import get_assistant_tool_options, get_llm_providers_for_assistants
from apps.custom_actions.form_utils import (
    clean_custom_action_operations,
    initialize_form_for_custom_actions,
    set_custom_actions,
)
from apps.experiments.models import AgentTools
from apps.files.forms import get_file_formset
from apps.generics.help import render_help_with_link
from apps.utils.prompt import validate_prompt_variables

INSTRUCTIONS_HELP_TEXT = """
    <p>Available variables to include in your prompt:</p>
    <p>{participant_data}: Optional</p>
    <p>{current_datetime}: Only required when the bot is using a tool</p>
"""


class OpenAiAssistantForm(forms.ModelForm):
    builtin_tools = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=get_assistant_tool_options())
    tools = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple,
        choices=AgentTools.user_tool_choices(include_end_session=False),
        required=False,
    )
    custom_action_operations = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = OpenAiAssistant
        fields = [
            "name",
            "instructions",
            "include_file_info",
            "builtin_tools",
            "tools",
            "llm_provider",
            "llm_provider_model",
            "temperature",
            "allow_file_search_attachments",
            "allow_code_interpreter_attachments",
            "allow_file_downloads",
            "top_p",
        ]
        labels = {
            "allow_file_search_attachments": "Allow ad-hoc files to be uploaded for file search",
            "allow_code_interpreter_attachments": "Allow ad-hoc files to be uploaded for code interpreter",
            "allow_file_downloads": "Allow files cited in the response to be downloaded",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["llm_provider"].queryset = get_llm_providers_for_assistants(request.team)
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProvider",
        }
        self.fields["llm_provider"].required = True
        self.fields["llm_provider_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"  # ty: ignore[invalid-assignment]
        self.fields["llm_provider_model"].required = True
        self.fields["include_file_info"].help_text = """If checked, extra information about uploaded files will
            be appended to the instructions. This will give the assistant knowledge about the file types."""
        self.fields["builtin_tools"].required = False
        self.fields["builtin_tools"].widget.attrs = {
            "x-model.fill": "builtinTools",
        }
        self.fields["instructions"].help_text = render_help_with_link(
            INSTRUCTIONS_HELP_TEXT, "concepts.prompt_variables"
        )
        initialize_form_for_custom_actions(request.team, self)

    def clean_custom_action_operations(self):
        return clean_custom_action_operations(self)

    def clean(self):
        cleaned_data = super().clean()
        validate_prompt_variables(
            context=cleaned_data,
            prompt_key="instructions",
            known_vars=OpenAiAssistant.ALLOWED_INSTRUCTIONS_VARIABLES,
        )
        return cleaned_data

    def save(self, commit=True):
        assistant = super().save(commit=False)
        assistant.team = self.request.team
        if commit:
            assistant.save()
            set_custom_actions(assistant, self.cleaned_data.get("custom_action_operations") or [])
            self.save_m2m()
        return assistant


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
