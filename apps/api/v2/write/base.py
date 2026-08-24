"""Shared plumbing for the v2 chatbot write endpoints."""

from rest_framework import serializers


class RejectsUnknownKeys:
    """Refuse a request body carrying keys this serializer does not declare.

    DRF's default is to drop them silently, which for a human is a typo they spot in the echoed
    response and for an agent is a 200 that wrote nothing. The consumer here is an agent, so a
    misspelled key has to be an error it can act on.

    Hooked into ``to_internal_value`` rather than ``validate`` because that is where a serializer
    sees its own raw input wherever it is mounted. A ``validate``-based check would have to read
    ``initial_data``, which DRF sets on the root serializer alone.
    """

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                accepted = ", ".join(sorted(self.fields))
                raise serializers.ValidationError(
                    {key: f"Unrecognised field. Accepted here: {accepted}." for key in unknown}
                )
        return super().to_internal_value(data)
