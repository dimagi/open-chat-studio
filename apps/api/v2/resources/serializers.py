"""Request and response serializers for the team's resources: source material and consent forms."""

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
        return SourceMaterial.objects.create(team=request.team, owner=request.user, **validated_data)


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


class ResourceArchivedSerializer(serializers.Serializer):
    """The archive response."""

    archived = serializers.BooleanField(help_text="Always true; the failure cases are status codes.")


class ArchiveRefusedSerializer(serializers.Serializer):
    """The archive 409: why the resource cannot be archived as things stand."""

    detail = serializers.CharField()


class ReferencingChatbotSerializer(serializers.Serializer):
    """A chatbot version whose pipeline uses the source material."""

    chatbot_id = serializers.UUIDField(help_text="The chatbot's id, as the chatbot endpoints take it.")
    version_number = serializers.IntegerField()
    published = serializers.BooleanField(
        help_text="True for the published version, whose nodes cannot be edited; false for the draft."
    )
    node_ids = serializers.ListField(
        child=serializers.CharField(), help_text="The nodes that use it, as the pipeline node endpoints take them."
    )


class ReferencingPipelineSerializer(serializers.Serializer):
    """A pipeline outside any listed chatbot, such as an archived chatbot's, that uses the source material."""

    name = serializers.CharField()


class SourceMaterialInUseSerializer(ArchiveRefusedSerializer):
    """The source material archive 409: where it is still used."""

    chatbots = ReferencingChatbotSerializer(many=True)
    other_pipelines = ReferencingPipelineSerializer(
        many=True, help_text="Only a person can remove these references, in the web app."
    )
