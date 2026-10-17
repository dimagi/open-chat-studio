"""Request and response serializers for a chatbot's triggers.

Validation is the web app's: a request body is flattened into the one mapping the event page posts,
and the same three forms judge it (``apps.events.services.TriggerForms``). The fields declared here
describe the request and response shape; they do not validate.
"""

from typing import Any, ClassVar

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.forms.models import model_to_dict
from rest_framework import serializers
from rest_framework.settings import api_settings

from apps.api.v2.inspect.serializers import InspectScheduledTriggerSerializer
from apps.api.v2.write.base import reject_unknown_keys
from apps.events.forms import (
    ACTION_PARAMS_FORMS,
    BaseTriggerForm,
    ScheduledTriggerForm,
    StaticTriggerForm,
    TimeoutTriggerForm,
)
from apps.events.models import EventAction, EventActionType, ScheduledTrigger, StaticTrigger, TimeoutTrigger
from apps.events.services import TriggerForms

from .params_reference import SERVER_SET_PARAMS, action_params_reference


class TriggerActionSerializer(serializers.ModelSerializer):
    """What a trigger does when it fires."""

    action_type = serializers.ChoiceField(
        choices=EventActionType.choices,
        required=False,
        help_text=(
            "What the trigger does. Required when adding a trigger; on update, leave it out to keep the current one."
        ),
    )
    params = serializers.DictField(
        required=False,
        help_text=(
            "The action's settings. Which keys it takes depends on `action_type`. On update, the "
            "params sent replace the stored ones rather than merging with them.\n\n" + action_params_reference()
        ),
    )

    class Meta:
        model = EventAction
        fields = ["action_type", "params"]

    def to_representation(self, instance: EventAction) -> dict:
        data = super().to_representation(instance)
        data["params"] = {key: value for key, value in data["params"].items() if key not in SERVER_SET_PARAMS}
        return data


class BaseTriggerSerializer(serializers.ModelSerializer):
    """A trigger and its action, validated by the web app's event forms.

    Expects ``chatbot`` in its context: the working chatbot the trigger belongs to.
    """

    trigger_form_class: ClassVar[type[BaseTriggerForm]]

    action = TriggerActionSerializer()
    is_active = serializers.BooleanField(
        read_only=True, help_text="Whether the trigger fires. It can only be switched off in the web app."
    )

    def to_internal_value(self, data: Any) -> dict:
        if not isinstance(data, dict):
            raise serializers.ValidationError({api_settings.NON_FIELD_ERRORS_KEY: ["Expected a JSON object."]})
        reject_unknown_keys(data, self.fields)
        trigger_forms = TriggerForms.bind(
            self.trigger_form_class,
            data=self._form_data(data),
            team_id=self.context["chatbot"].team_id,
            experiment_id=self.context["chatbot"].id,
            trigger=self.instance,
        )
        if not trigger_forms.is_valid():
            raise serializers.ValidationError(_form_errors(trigger_forms))
        return {"trigger_forms": trigger_forms}

    def create(self, validated_data: dict):
        return validated_data["trigger_forms"].save()

    def update(self, instance, validated_data: dict):
        return self.create(validated_data)

    def _form_data(self, data: dict) -> dict:
        """The body as the one flat mapping the event page posts, with what is not sent kept as stored."""
        trigger_fields = list(self.trigger_form_class.base_fields)
        stored = model_to_dict(self.instance, fields=trigger_fields) if self.instance else {}
        trigger_data = {**stored, **{key: value for key, value in data.items() if key in trigger_fields}}
        action_type, params = self._action_data(data)
        return {**params, **trigger_data, "action_type": action_type}

    def _action_data(self, data: dict) -> tuple[str, dict]:
        """The action type and params to validate: those sent, or the stored ones when ``action`` is left out."""
        stored_action = self.instance.action if self.instance else None
        if "action" not in data and stored_action:
            return stored_action.action_type, stored_action.params
        action = data.get("action")
        if not isinstance(action, dict):
            message = "This field is required." if action is None else "Expected a JSON object."
            raise serializers.ValidationError({"action": [message]})
        reject_unknown_keys(action, self.fields["action"].fields, path=("action",))
        action_type = _valid_action_type(action.get("action_type") or getattr(stored_action, "action_type", None))
        return action_type, _valid_params(action.get("params", {}), action_type=action_type)


class StaticTriggerWriteSerializer(BaseTriggerSerializer):
    trigger_form_class = StaticTriggerForm

    class Meta:
        model = StaticTrigger
        fields = ["id", "type", "is_active", "action"]
        extra_kwargs = {"type": {"help_text": "The conversation event that fires the trigger."}}


class TimeoutTriggerWriteSerializer(BaseTriggerSerializer):
    trigger_form_class = TimeoutTriggerForm

    class Meta:
        model = TimeoutTrigger
        fields = ["id", "delay", "total_num_triggers", "trigger_from_first_message", "is_active", "action"]
        extra_kwargs = {
            "delay": {"help_text": "Seconds of participant inactivity before the trigger fires."},
            "total_num_triggers": {"help_text": "How many times the trigger fires in one session."},
            "trigger_from_first_message": {
                "help_text": (
                    "Measure the delay from the participant's first message (true) rather than their "
                    "most recent one (false)."
                )
            },
        }


class ScheduledTriggerWriteSerializer(BaseTriggerSerializer):
    trigger_form_class = ScheduledTriggerForm

    class Meta:
        model = ScheduledTrigger
        fields = InspectScheduledTriggerSerializer.Meta.fields
        read_only_fields = ["scheduled_at", "fired_at"]
        extra_kwargs = InspectScheduledTriggerSerializer.Meta.extra_kwargs


def _valid_action_type(action_type: Any) -> str:
    """Checked before the forms run because the params form, and so the params accepted, is chosen by type."""
    if action_type is None:
        raise serializers.ValidationError({"action": {"action_type": ["This field is required."]}})
    if not isinstance(action_type, str) or action_type not in ACTION_PARAMS_FORMS:
        raise serializers.ValidationError({"action": {"action_type": [f'"{action_type}" is not a valid choice.']}})
    return action_type


def _valid_params(params: Any, *, action_type: str) -> dict:
    if not isinstance(params, dict):
        raise serializers.ValidationError({"action": {"params": ["Expected a JSON object."]}})
    accepted = set(ACTION_PARAMS_FORMS[action_type].base_fields) - set(SERVER_SET_PARAMS)
    reject_unknown_keys(params, accepted, path=("action", "params"))
    return params


def _form_errors(trigger_forms: TriggerForms) -> dict:
    """The forms' errors in the request body's shape: the action's under ``action``, its params' under ``params``."""
    errors = _as_drf_errors(trigger_forms.trigger_form)
    action_errors = _as_drf_errors(trigger_forms.action_form)
    if params_errors := _as_drf_errors(trigger_forms.params_form):
        action_errors["params"] = params_errors
    if action_errors:
        errors["action"] = action_errors
    return errors


def _as_drf_errors(form: forms.BaseForm) -> dict:
    return {
        (api_settings.NON_FIELD_ERRORS_KEY if field == NON_FIELD_ERRORS else field): list(messages)
        for field, messages in form.errors.items()
    }
