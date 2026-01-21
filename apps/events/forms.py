from django import forms
from django.db.models import Q, Subquery

from apps.events.models import TimePeriod
from apps.experiments.models import Experiment, ExperimentRoute
from apps.generics.type_select_form import TypeSelectForm
from apps.pipelines.models import Pipeline, PipelineEventInputs

from .models import EventAction, StaticTrigger, TimeoutTrigger


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
    input_type = forms.ChoiceField(
        choices=PipelineEventInputs.choices,
        label="Input to pipeline",
        required=True,
    )

    def __init__(self, *args, **kwargs):
        team_id = kwargs.pop("team_id")
        super().__init__(*args, **kwargs)
        self.fields["pipeline_id"].queryset = Pipeline.objects.filter(team_id=team_id, working_version_id__isnull=True)

    def clean_pipeline_id(self):
        return self.cleaned_data["pipeline_id"].id


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
        min_value=0,
        help_text="Indicates how many times this should go on for. Specify '0' for a one time event",
    )
    experiment_id = forms.ChoiceField(
        label="Experiment", help_text="Select the experiment to process this scheduled message"
    )

    def __init__(self, *args, **kwargs):
        experiment_id = kwargs.pop("experiment_id")
        non_required_fields = kwargs.pop("non_required_fields", [])
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

        for field_name in non_required_fields:
            self.fields[field_name].required = False


class EventActionForm(forms.ModelForm):
    class Meta:
        model = EventAction
        fields = ["action_type"]
        labels = {"action_type": "Then..."}

    def clean(self):
        cleaned_data = super().clean()
        action_type = cleaned_data.get("action_type")

        if self.data and "type" in self.data:
            trigger_type = self.data.get("type")
            if trigger_type == "new_bot_message" and action_type in [
                "send_message_to_bot",
                "schedule_trigger",
            ]:
                raise forms.ValidationError("This action is not allowed when 'A new bot message is received'")
        return cleaned_data

    def save(self, commit=True, experiment_id=None):
        instance = super().save(commit=False)
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


def get_action_params_form(data=None, instance=None, team_id=None, experiment_id=None):
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
            "schedule_trigger": ScheduledMessageConfigForm(experiment_id=experiment_id, **form_kwargs),
            "pipeline_start": PipelineStartForm(team_id=team_id, **form_kwargs),
        },
        secondary_key_field="action_type",
    )


class BaseTriggerForm(forms.ModelForm):
    def save(self, commit=True, experiment_id=None):
        instance = super().save(commit=False)
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
