from django import forms

from .models import EventAction, StaticTrigger, TimeoutTrigger


class EventActionForm(forms.ModelForm):
    class Meta:
        model = EventAction
        fields = ["action_type", "params"]
        labels = {"action_type": "Then...", "params": "With the following parameters:"}

    def save(self, commit=True, *args, **kwargs):
        experiment_id = kwargs.pop("experiment_id")
        instance = super().save(commit=False, *args, **kwargs)
        instance.experiment_id = experiment_id
        if commit:
            instance.save()
        return instance


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
