"""Serializer fields shared by the v2 write endpoints."""

import unicodedata
from collections.abc import Callable

from django.db.models import QuerySet
from rest_framework import serializers
from rest_framework.request import Request


class NfcCharField(serializers.CharField):
    """A string normalised to NFC, so API- and UI-written values compare equal.

    Mirrors ``CreateChatbot.form_valid``, which normalises the chatbot name the same way.

    Normalisation belongs in ``to_internal_value`` rather than a ``validate_<field>`` hook: hooks run
    *after* the field's own validators, and NFC can make a string **longer** -- U+0958 is a
    composition exclusion, so it normalises to the two code points U+0915 U+093C. A 128-character
    name of those would clear ``max_length=128`` and then overflow ``varchar(128)`` on insert, which
    is a 500 for what is only an over-long name.
    """

    def to_internal_value(self, data):
        return unicodedata.normalize("NFC", super().to_internal_value(data))


class OptionalTextField(serializers.CharField):
    """Free text that may be sent blank or null, and is always stored as ``""``.

    ``Experiment.description`` is ``TextField(null=True, default="")`` with no ``blank=True``, so
    ``ModelSerializer`` would generate ``allow_blank=False``. Every chatbot this API creates starts
    with ``description=""``, so a client echoing a response of ours straight back at us would be
    refused on a field it never meant to change -- and the consumer here is an agent, for which
    read-modify-write is the normal way to edit.

    Null is accepted because rows predating this endpoint may hold SQL NULL, and is normalised to
    ``""`` so only the one representation is ever written. That is what ``ChatbotSettingsForm``
    writes too: its ``description`` is ``required=False``, which cleans blank input to ``""``.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_blank", True)
        kwargs.setdefault("allow_null", True)
        super().__init__(**kwargs)

    def validate_empty_values(self, data):
        # `to_internal_value` never sees None: `run_validation` short-circuits on it when
        # `allow_null` is set, so the coercion has to happen here.
        is_empty, value = super().validate_empty_values(data)
        if is_empty and value is None:
            return True, ""
        return is_empty, value


class TeamScopedRelatedField(serializers.PrimaryKeyRelatedField):
    """A reference restricted to the rows this request may point at.

    The queryset is resolved at validation time rather than at construction: a nested serializer's
    fields are built before they are bound to a parent, so ``__init__`` cannot reach the request.
    ``RelatedField`` skips its "must provide a queryset" assertion when ``get_queryset`` is
    overridden.

    ``scoped_queryset`` is handed the whole request, not just the team, because team membership is
    not always the only thing that narrows the set -- the voice fields also have to apply the same
    feature flag ``ChatbotSettingsForm`` applies, and a flag can be active for reasons that have
    nothing to do with the team (see ``excluded_voice_services``).

    A cross-team id simply misses the queryset, exactly as a nonexistent id does, so both surface
    as a 400 on the field. Telling them apart would need a second, global query whose only effect
    is to leak whether the id exists in another team.
    """

    def __init__(self, scoped_queryset: Callable[[Request], QuerySet], **kwargs):
        self.scoped_queryset = scoped_queryset
        super().__init__(**kwargs)

    def get_queryset(self):
        return self.scoped_queryset(self.context["request"])
