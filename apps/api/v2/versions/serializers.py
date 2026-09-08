"""Request and response shapes for the chatbot version endpoints (#4142)."""

from django.db import models
from rest_framework import serializers

from apps.api.v2.inspect.serializers import PipelineBuildErrorsSerializer
from apps.api.v2.write.base import RejectsUnknownKeys
from apps.api.v2.write.fields import OptionalTextField


class VersionCreateSerializer(RejectsUnknownKeys, serializers.Serializer):
    """The version-create body: whether the snapshot goes live, and what to label it."""

    make_default = serializers.BooleanField(
        default=False,
        help_text=(
            "Whether the new version becomes the one participants are served, which takes effect "
            "from the next message on. Left false, the version is a checkpoint that nothing serves "
            "-- except a chatbot's first version, which is always the published one and so is "
            "always held to the pipeline-validity gate."
        ),
    )
    version_description = OptionalTextField(
        default="",
        help_text=(
            "Free-text label for this version -- what changed since the last one. The "
            "`chatbot_inspect` endpoint reads it back under the same name."
        ),
    )


class VersionCreateRefusedSerializer(serializers.Serializer):
    """The 422: why the chatbot's state made the request unanswerable."""

    detail = serializers.CharField()
    pipeline_errors = PipelineBuildErrorsSerializer(
        required=False,
        help_text=(
            "The repair list, present only when going live was refused for a pipeline that does "
            "not validate. Absent when the refusal is that there is nothing to snapshot."
        ),
    )


class VersionArchivedSerializer(serializers.Serializer):
    """The version-archive response: that it happened."""

    archived = serializers.BooleanField(help_text="Always true; the failure cases are status codes.")


class PublishVersionSerializer(RejectsUnknownKeys, serializers.Serializer):
    """The PATCH body: make this version the one participants are served.

    Named as ``chatbot_inspect`` reports it, so an agent that read
    ``is_published_version: false`` writes back the same key.
    """

    is_published_version = serializers.BooleanField(
        help_text=(
            "Must be `true`. A chatbot may hold only one published version and always needs one, "
            "so this promotes a version; it cannot un-publish one. Create a new version that goes "
            "live, or make a different version the published one, to move it off."
        )
    )

    def validate_is_published_version(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "Only `true` is accepted. A chatbot always needs a published version, so this "
                "cannot un-publish one -- make a different version the published one instead."
            )
        return value


class VersionPublishedSerializer(serializers.Serializer):
    """The PATCH response: which version participants are served now."""

    version_number = serializers.IntegerField(help_text="The version now served to participants.")
    is_published_version = serializers.BooleanField(help_text="Always true; the failure cases are status codes.")


class VersionStatus(models.TextChoices):
    """The states a version poll reports. ``pending`` is not terminal; ``completed`` is.

    ``TextChoices`` rather than a plain ``StrEnum`` for a set that backs no model column, because
    ``ENUM_NAME_OVERRIDES`` (config/settings.py) is what gives this a stable name in the published
    schema, and it matches on the ``(value, label)`` pairs a ``Choices`` class produces.
    """

    PENDING = "pending"
    COMPLETED = "completed"


class VersionStatusSerializer(serializers.Serializer):
    """A version poll's answer: whether an operation is running, and the newest version if not."""

    status = serializers.ChoiceField(
        choices=VersionStatus.choices,
        help_text="`pending` while a version operation is running -- poll again. `completed` is terminal.",
    )
    version_number = serializers.IntegerField(
        required=False,
        help_text="The chatbot's newest version. Present on `completed` only.",
    )
