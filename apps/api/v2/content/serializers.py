"""Request and response serializers for the team's content resources: source material and consent forms."""

from rest_framework import serializers

from apps.api.v2.write.base import RejectsUnknownKeys
from apps.api.v2.write.fields import OptionalTextField
from apps.experiments.models import ConsentForm, SourceMaterial


class SourceMaterialResourceSerializer(RejectsUnknownKeys, serializers.ModelSerializer):
    """The fields the web app's source material form edits, plus the id nodes reference it by."""

    description = OptionalTextField(label=SourceMaterial._meta.get_field("description").verbose_name)

    class Meta:
        model = SourceMaterial
        fields = ["id", "topic", "description", "material"]

    def create(self, validated_data) -> SourceMaterial:
        request = self.context["request"]
        return SourceMaterial.objects.create(
            team=request.team,
            # A client-credentials (machine) token has no user behind it.
            owner=request.user if request.user.is_authenticated else None,
            **validated_data,
        )


class ConsentFormResourceSerializer(RejectsUnknownKeys, serializers.ModelSerializer):
    """The fields the web app's consent form editor edits, plus the id a chatbot references it by."""

    class Meta:
        model = ConsentForm
        fields = [
            "id",
            "name",
            "consent_text",
            "capture_identifier",
            "identifier_label",
            "identifier_type",
            "confirmation_text",
            "is_default",
        ]
        extra_kwargs = {
            "is_default": {
                "help_text": (
                    "Whether this is the team's default consent form. The default cannot be archived, "
                    "and chatbots using an archived form are moved onto it."
                )
            }
        }

    def create(self, validated_data) -> ConsentForm:
        return ConsentForm.objects.create(team=self.context["request"].team, **validated_data)


class ContentArchivedSerializer(serializers.Serializer):
    """The archive response."""

    archived = serializers.BooleanField(help_text="Always true; the failure cases are status codes.")


class ArchiveRefusedSerializer(serializers.Serializer):
    """The archive 409: why the resource cannot be archived as things stand."""

    detail = serializers.CharField()
