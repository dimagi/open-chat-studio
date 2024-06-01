from django import forms
from langchain.memory.prompt import SUMMARY_PROMPT

from apps.events.models import TimePeriod
from apps.generics.type_select_form import TypeSelectForm
from apps.pipelines.models import Pipeline

from .models import EventAction, StaticTrigger, TimeoutTrigger


class SummarizeConversationForm(forms.Form):
    prompt = forms.CharField(
        widget=forms.Textarea, label="With the following prompt:", required=False, initial=SUMMARY_PROMPT.template
    )

    def clean_prompt(self):
        data = self.cleaned_data["prompt"]
        if not data:
            return SUMMARY_PROMPT.template
        return data


class SendMessageToBotForm(forms.Form):
    message_to_bot = forms.CharField(
        widget=forms.Textarea,
        label="With the following prompt:",
        required=False,
        initial="The user hasn't responded, please prompt them again.",
    )

    def clean_message_to_bot(self):
        data = self.cleaned_data["message_to_bot"]
        if not data:
            return "The user hasn't responded, please prompt them again."
        return data


class PipelineStartForm(forms.Form):
    pipeline_id = forms.ModelChoiceField(
        queryset=None,
        label="Select a pipeline",
        required=True,
    )

    def __init__(self, *args, **kwargs):
        team_id = kwargs.pop("team_id")
        super().__init__(*args, **kwargs)
        self.fields["pipeline_id"].queryset = Pipeline.objects.filter(team_id=team_id)

    def clean_pipeline_id(self):
        return self.cleaned_data["pipeline_id"].id


class EmptyForm(forms.Form):
    pass


class ScheduledMessageConfigForm(forms.Form):
    prompt_text = forms.CharField(
        label="Bot's instructions",
        help_text="Instructions for the bot to formulate a response",
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    frequency = forms.IntegerField(label="Every...", min_value=1)
    time_period = forms.ChoiceField(label="Time period", choices=TimePeriod.choices)
    repetitions = forms.IntegerField(
        label="Repetitions",
        min_value=1,
        help_text="Indicates how many times this should go on for. Specify '1' for a one time event",
    )

    def __init__(self, *args, **kwargs):
        if "initial" not in kwargs:
            kwargs["initial"] = {"frequency": 1, "repetitions": 1, "time_period": TimePeriod.WEEKS}
        super().__init__(*args, **kwargs)


class EventActionForm(forms.ModelForm):
    class Meta:
        model = EventAction
        fields = ["action_type"]
        labels = {"action_type": "Then..."}

    def save(self, commit=True, *args, **kwargs):
        experiment_id = kwargs.pop("experiment_id")
        instance = super().save(commit=False, *args, **kwargs)
        instance.experiment_id = experiment_id
        if commit:
            instance.save()
        return instance


class EventActionTypeSelectForm(TypeSelectForm):
    def save(self, *args, **kwargs):
        instance = self.primary.save(*args, **kwargs, commit=False)
        instance.params = self.active_secondary().cleaned_data
        instance.save()
        return instance


def get_action_params_form(data=None, instance=None, team_id=None):
    form_kwargs = {
        "data": data,
        "initial": instance.params if instance else None,
    }
    return EventActionTypeSelectForm(
        primary=EventActionForm(data=data, instance=instance),
        secondary={
            "log": EmptyForm(**form_kwargs),
            "send_message_to_bot": SendMessageToBotForm(**form_kwargs),
            "end_conversation": EmptyForm(**form_kwargs),
            "summarize": SummarizeConversationForm(**form_kwargs),
            "schedule_trigger": ScheduledMessageConfigForm(**form_kwargs),
            "pipeline_start": PipelineStartForm(team_id=team_id, **form_kwargs),
        },
        secondary_key_field="action_type",
    )


class BaseTriggerForm(forms.ModelForm):
    def save(self, commit=True, *args, **kwargs):
        experiment_id = kwargs.pop("experiment_id")
        instance = super().save(commit=False, *args, **kwargs)
        instance.experiment_id = experiment_id
        if commit:
            instance.save()
        return instance


class StaticTriggerForm(BaseTriggerForm):
    class Meta:
        model = StaticTrigger
        fields = ["type"]
        labels = {"type": "When..."}


class TimeoutTriggerForm(BaseTriggerForm):
    class Meta:
        model = TimeoutTrigger
        fields = ["delay", "total_num_triggers"]
        labels = {"total_num_triggers": "Trigger count", "delay": "Wait time"}
