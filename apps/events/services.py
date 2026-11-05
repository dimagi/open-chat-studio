"""Validating and saving an event trigger and its action, for the web app and the API alike."""

from dataclasses import dataclass

from django import forms
from django.db import transaction

from apps.events.forms import (
    ACTION_PARAMS_FORMS,
    BaseTriggerForm,
    EventActionForm,
    build_action_params_form,
)


@dataclass
class TriggerForms:
    """The three forms one trigger is edited through: the trigger, its action, and the action's params."""

    trigger_form: BaseTriggerForm
    action_form: EventActionForm
    params_form: forms.Form
    action_type: str
    experiment_id: int | str

    @classmethod
    def bind(
        cls,
        trigger_form_class: type[BaseTriggerForm],
        *,
        data=None,
        team_id: int,
        experiment_id: int | str,
        trigger=None,
    ) -> "TriggerForms":
        """Build the forms for a new trigger, or for ``trigger`` when editing one.

        ``data`` is one flat mapping holding every form's fields, as the web app's page posts them.
        ``EventActionForm`` reads the trigger's ``type`` from it to refuse actions that cannot follow
        that trigger.
        """
        action = trigger.action if trigger else None
        stored_action_type = action.action_type if action else next(iter(ACTION_PARAMS_FORMS))
        action_type = (data or {}).get("action_type") or stored_action_type
        return cls(
            trigger_form=trigger_form_class(data, instance=trigger),
            action_form=EventActionForm(data, instance=action),
            params_form=build_action_params_form(
                action_type,
                data=data,
                initial=action.params if action else None,
                team_id=team_id,
                experiment_id=experiment_id,
            ),
            action_type=action_type,
            experiment_id=experiment_id,
        )

    def is_valid(self) -> bool:
        # Every form is validated, rather than stopping at the first invalid one, so all their errors
        # are reported together.
        valid = [form.is_valid() for form in (self.action_form, self.params_form, self.trigger_form)]
        return all(valid)

    @transaction.atomic()
    def save(self):
        """Save the action and the trigger. Call only after ``is_valid`` returns True."""
        action = self.action_form.save(commit=False, experiment_id=self.experiment_id)
        action.params = self.params_form.cleaned_data
        action.save()
        trigger = self.trigger_form.save(commit=False, experiment_id=self.experiment_id)
        trigger.action = action
        trigger.save()
        return trigger
