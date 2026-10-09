"""OpenAPI descriptions for the resource endpoints."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.api.v2.resources.serializers import (
    ArchiveRefusedSerializer,
    ResourceArchivedSerializer,
    SourceMaterialInUseSerializer,
)


def resource_schema(
    *, noun: str, plural: str, archive_refusal: str, archive_refused_serializer=ArchiveRefusedSerializer
):
    """The OpenAPI descriptions shared by both resource viewsets."""
    operation_prefix = noun.lower().replace(" ", "_")
    tag = plural
    machine_tokens = " A client-credentials token can read but not write; its writes answer `403`."
    id_parameter = OpenApiParameter(
        name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, description=f"{noun} ID"
    )
    return extend_schema_view(
        list=extend_schema(
            operation_id=f"{operation_prefix}_list",
            summary=f"List {plural}",
            description=f"List the team's {plural.lower()}. Archived ones are not listed.",
            tags=[tag],
        ),
        retrieve=extend_schema(
            operation_id=f"{operation_prefix}_retrieve",
            parameters=[id_parameter],
            summary=f"Retrieve {noun}",
            description=f"Retrieve the {noun.lower()} with this ID. An archived one answers `404`.",
            tags=[tag],
        ),
        create=extend_schema(
            operation_id=f"{operation_prefix}_create",
            summary=f"Create {noun}",
            description="A key that is not listed below is rejected rather than ignored." + machine_tokens,
            tags=[tag],
        ),
        partial_update=extend_schema(
            operation_id=f"{operation_prefix}_update",
            parameters=[id_parameter],
            summary=f"Update {noun}",
            description=(
                "Only the keys you send are changed, and a key that is not listed below is rejected "
                "rather than ignored. Published chatbot versions keep the content they were "
                "published with; publish the chatbot again to pick up the edit." + machine_tokens
            ),
            tags=[tag],
        ),
        destroy=extend_schema(
            operation_id=f"{operation_prefix}_archive",
            parameters=[id_parameter],
            summary=f"Archive {noun}",
            description=(
                f"Archive the {noun.lower()}. This is a soft delete: it is hidden rather than "
                "destroyed, and a person can restore it in the web app. A repeat of this call "
                f"answers `404`, which makes it safe to retry. {archive_refusal}" + machine_tokens
            ),
            tags=[tag],
            request=None,
            responses={
                200: ResourceArchivedSerializer,
                404: OpenApiResponse(description="No such resource, or it is archived already."),
                409: archive_refused_serializer,
            },
        ),
    )


source_material_schema = resource_schema(
    noun="Source Material",
    plural="Source Material",
    archive_refusal=(
        "It is refused with `409` while a pipeline node or a live chatbot version still uses it. "
        "The response lists each chatbot version and node that uses it."
    ),
    archive_refused_serializer=SourceMaterialInUseSerializer,
)

consent_form_schema = resource_schema(
    noun="Consent Form",
    plural="Consent Forms",
    archive_refusal=(
        "Every chatbot draft using the form is moved onto the team's default consent form; published "
        "versions keep the form they were published with. The default form itself cannot be archived "
        "and answers `409`."
    ),
)
