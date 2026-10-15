"""Request and response serializers for the team's resources: source material and consent forms."""

from rest_framework import serializers

from apps.api.serializers import ErrorDetailSerializer
from apps.api.v2.write.base import RejectsUnwritableKeys
from apps.api.v2.write.fields import OptionalTextField
from apps.experiments.models import ConsentForm, SourceMaterial


class SourceMaterialResourceSerializer(RejectsUnwritableKeys, serializers.ModelSerializer):
    """The fields the web app's source material form edits, plus the id nodes reference it by."""

    description = OptionalTextField(label=SourceMaterial._meta.get_field("description").verbose_name)

    class Meta:
        model = SourceMaterial
        fields = ["id", "topic", "description", "material"]

    def create(self, validated_data) -> SourceMaterial:
        request = self.context["request"]
        owner = request.user if request.user.is_authenticated else None
        return SourceMaterial.objects.create(team=request.team, owner=owner, **validated_data)


class ConsentFormResourceSerializer(RejectsUnwritableKeys, serializers.ModelSerializer):
    """The consent form fields that channels show, plus the id a chatbot references it by."""

    class Meta:
        model = ConsentForm
        # The participant identifier fields are left out: only the public web chat reads them, and it is
        # being removed (#4209).
        fields = ["id", "name", "consent_text", "confirmation_text", "is_default"]
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


class SourceMaterialInUseSerializer(ErrorDetailSerializer):
    """The source material archive 409: where it is still used."""

    chatbots = ReferencingChatbotSerializer(many=True)
    other_pipelines = ReferencingPipelineSerializer(
        many=True, help_text="Only a person can remove these references, in the web app."
    )
