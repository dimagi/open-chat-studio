from django import forms
from django.db.models import Q, Subquery
from langchain.memory.prompt import SUMMARY_PROMPT

from apps.events.models import TimePeriod
from apps.experiments.models import Experiment, ExperimentRoute
from apps.generics.type_select_form import TypeSelectForm

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


class EmptyForm(forms.Form):
    pass


class ScheduledMessageConfigForm(forms.Form):
    name = forms.CharField(label="Name", help_text="Descriptive name for this schedule")
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
    experiment_id = forms.ChoiceField(
        label="Experiment", help_text="Select the experiment to process this scheduled message"
    )

    def __init__(self, *args, **kwargs):
        experiment_id = kwargs.pop("experiment_id")
        super().__init__(*args, **kwargs)

        field = self.fields["experiment_id"]
        children_subquery = Subquery(
            ExperimentRoute.objects.filter(parent__id=experiment_id).values_list("child", flat=True)
        )
        experiments = Experiment.objects.filter(Q(id=experiment_id) | Q(id__in=children_subquery)).values_list(
            "id", "name"
        )
        field.choices = experiments
        if not kwargs.get("initial") and len(experiments) == 1:
            field.initial = experiment_id
            field.widget = field.hidden_widget()


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


def get_action_params_form(data=None, instance=None, experiment_id=None):
    initial = instance.params if instance else None
    return EventActionTypeSelectForm(
        primary=EventActionForm(data=data, instance=instance),
        secondary={
            "log": EmptyForm(data=data, initial=initial),
            "send_message_to_bot": SendMessageToBotForm(data=data, initial=initial),
            "end_conversation": EmptyForm(data=data, initial=initial),
            "summarize": SummarizeConversationForm(data=data, initial=initial),
            "schedule_trigger": ScheduledMessageConfigForm(data=data, initial=initial, experiment_id=experiment_id),
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
