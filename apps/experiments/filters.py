import operator
from collections.abc import Sequence
from typing import ClassVar

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, Subquery

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Experiment
from apps.web.dynamic_filters.base import (
    DATE_RANGE_OPTIONS,
    TYPE_CHOICE,
    ChoiceColumnFilter,
    ColumnFilter,
    MultiColumnFilter,
)
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    ParticipantFilter,
    RemoteIdFilter,
    StatusFilter,
    TimestampFilter,
)


def get_filter_context_data(
    team, columns: dict[str, dict], date_range_column: str, table_url: str, table_container_id: str
):
    if date_range_column not in columns:
        raise ValueError("Date range column is not present in list of columns")
    return {
        "df_date_range_options": DATE_RANGE_OPTIONS,
        "df_channel_list": ChannelPlatform.for_filter(team),
        "df_filter_columns": columns,
        "df_date_range_column_name": date_range_column,
        "df_filter_data_source_url": table_url,
        "df_filter_data_source_container_id": table_container_id,
    }


class ChatMessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "tags"
    label: str = "Tags"
    type: str = TYPE_CHOICE

    def prepare(self, team, **_):
        self.options = [tag.name for tag in team.tag_set.filter(is_system_tag=False)]

    def apply_any_of(self, queryset, value, timezone=None):
        chat_tags_condition = Q(chat__tags__name__in=value)
        message_tags_condition = Q(chat__messages__tags__name__in=value)
        return queryset.filter(chat_tags_condition | message_tags_condition)

    def apply_all_of(self, queryset, value, timezone=None):
        conditions = Q()
        chat_content_type = ContentType.objects.get_for_model(Chat)
        chat_message_content_type = ContentType.objects.get_for_model(ChatMessage)

        for tag in value:
            chat_tag_exists = Exists(
                CustomTaggedItem.objects.filter(
                    object_id=OuterRef("chat_id"),
                    content_type_id=chat_content_type.id,
                    tag__name=tag,
                )
            )
            message_tag_exists = Exists(
                CustomTaggedItem.objects.filter(
                    content_type_id=chat_message_content_type.id,
                    tag__name=tag,
                    object_id__in=Subquery(
                        ChatMessage.objects.filter(chat_id=OuterRef(OuterRef("chat_id"))).values("id")
                    ),
                )
            )
            conditions &= chat_tag_exists | message_tag_exists
        return queryset.filter(conditions)

    def apply_excludes(self, queryset, value, timezone=None):
        chat_tags_condition = Q(chat__tags__name__in=value)
        message_tags_condition = Q(chat__messages__tags__name__in=value)
        return queryset.exclude(chat_tags_condition | message_tags_condition)


class VersionsFilter(ChoiceColumnFilter):
    query_param: str = "versions"
    label: str = "Versions"

    def prepare(self, team, **kwargs):
        single_experiment = kwargs.get("single_experiment")
        self.options = Experiment.objects.get_version_names(team, working_version=single_experiment)

    def _get_messages_queryset(self, tags, operator):
        combined_query = Q()
        for tag in tags:
            queryset = ChatMessage.objects.filter(
                chat=OuterRef("chat"),
                tags__name=tag,
                tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
            ).values("id")
            combined_query = operator(combined_query, Q(Exists(queryset)))
        return combined_query

    def apply_any_of(self, queryset, value, timezone=None):
        combined_query = self._get_messages_queryset(value, operator.or_)
        return queryset.filter(combined_query)

    def apply_excludes(self, queryset, value, timezone=None):
        combined_query = self._get_messages_queryset(value, operator.or_)
        return queryset.exclude(combined_query)

    def apply_all_of(self, queryset, value, timezone=None):
        combined_query = self._get_messages_queryset(value, operator.and_)
        return queryset.filter(combined_query)


class ChannelsFilter(ChoiceColumnFilter):
    query_param: str = "channels"
    label: str = "Channels"
    column: str = "experiment_channel__platform"

    def prepare(self, team, **_):
        self.options = ChannelPlatform.for_filter(team)

    def parse_query_value(self, query_value) -> any:
        selected_display_names = self.values_list(query_value)
        if not selected_display_names:
            return None

        display_to_value = {label: val for val, label in ChannelPlatform.choices}
        selected_values = [display_to_value.get(name.strip()) for name in selected_display_names]
        return [val for val in selected_values if val is not None]


class ExperimentSessionFilter(MultiColumnFilter):
    """Filter for experiment sessions using the new ColumnFilter pattern."""

    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(label="Last Message", column="last_message_created_at", query_param="last_message"),
        TimestampFilter(label="First Message", column="first_message_created_at", query_param="first_message"),
        TimestampFilter(label="Message Date", column="chat__messages__created_at", query_param="message_date"),
        ChatMessageTagsFilter(),
        VersionsFilter(),
        ChannelsFilter(),
        ExperimentFilter(),
        StatusFilter(query_param="state"),
        RemoteIdFilter(),
    ]

    def prepare_queryset(self, queryset):
        """Prepare the queryset by annotating with first and last message timestamps."""
        first_message_subquery = (
            ChatMessage.objects.filter(
                chat__experiment_session=OuterRef("pk"),
            )
            .order_by("created_at")
            .values("created_at")[:1]
        )

        last_message_subquery = (
            ChatMessage.objects.filter(
                chat__experiment_session=OuterRef("pk"),
            )
            .order_by("-created_at")
            .values("created_at")[:1]
        )

        queryset = queryset.annotate(first_message_created_at=Subquery(first_message_subquery))
        queryset = queryset.annotate(last_message_created_at=Subquery(last_message_subquery))
        return queryset

import json
from django.db import transaction, models
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.filters.models import FilterSet
from apps.filters.serializers import (
    FilterSetSerializer,
    FilterSetCreateUpdateSerializer,
)
from apps.teams.decorators import login_and_team_required


def _serialize_filter_set(fs: FilterSet) -> dict:
    return FilterSetSerializer(
        instance={
            "id": fs.id,
            "name": fs.name,
            "table_type": fs.table_type,
            "filter_params": fs.filter_params,
            "is_shared": fs.is_shared,
            "is_starred": fs.is_starred,
            "is_default_for_user": fs.is_default_for_user,
            "is_default_for_team": fs.is_default_for_team,
        }
    ).data


@require_http_methods(["GET"])
@login_and_team_required
def list_filter_sets(request, team_slug: str, table_type: str):
    qs = (
        FilterSet.objects.filter(
            team=request.team,
            table_type=table_type,
            is_deleted=False,
        )
        .filter(models.Q(user=request.user) | models.Q(is_shared=True))
        .order_by("-is_starred", "name")
    )
    data = [_serialize_filter_set(fs) for fs in qs.all()]
    return JsonResponse({"results": data})


@require_http_methods(["POST"])
@login_and_team_required
def create_filter_set(request, team_slug: str, table_type: str):
    payload = json.loads(request.body or b"{}")
    serializer = FilterSetCreateUpdateSerializer(data=payload, context={"is_team_admin": request.team_membership.is_team_admin})
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)
    validated = serializer.validated_data

    with transaction.atomic():
        if validated.get("is_default_for_user"):
            FilterSet.objects.filter(
                team=request.team, user=request.user, table_type=table_type, is_default_for_user=True
            ).update(is_default_for_user=False)
        if validated.get("is_default_for_team"):
            FilterSet.objects.filter(
                team=request.team, table_type=table_type, is_default_for_team=True
            ).update(is_default_for_team=False)
        fs = FilterSet.objects.create(
            team=request.team,
            user=request.user,
            name=validated.get("name", "").strip(),
            table_type=table_type,
            filter_params=validated.get("filter_params", {}),
            is_shared=validated.get("is_shared", False),
            is_starred=validated.get("is_starred", False),
            is_default_for_user=validated.get("is_default_for_user", False),
            is_default_for_team=validated.get("is_default_for_team", False),
        )
    return JsonResponse({"result": _serialize_filter_set(fs)}, status=201)


@require_http_methods(["PATCH", "DELETE"])
@login_and_team_required
def edit_or_delete_filter_set(request, team_slug: str, pk: int):
    try:
        fs = FilterSet.objects.get(team=request.team, id=pk, is_deleted=False)
    except FilterSet.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    is_owner = fs.user == request.user
    is_team_admin = request.team_membership.is_team_admin

    if request.method == "DELETE":
        # Only owner or team admin can delete
        if not (is_owner or is_team_admin):
            return JsonResponse({"error": "You don't have permission to delete this filter set"}, status=403)
        fs.is_deleted = True
        fs.save(update_fields=["is_deleted"])
        return JsonResponse({"success": True})

    payload = json.loads(request.body or b"{}")
    serializer = FilterSetCreateUpdateSerializer(
        data=payload, partial=True, context={"is_team_admin": request.team_membership.is_team_admin}
    )
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)
    validated = serializer.validated_data

    with transaction.atomic():
        updates = []
        if "filter_params" in validated:
            fs.filter_params = validated["filter_params"]
            updates.append("filter_params")
        if "is_shared" in validated:
            fs.is_shared = bool(validated["is_shared"])
            updates.append("is_shared")
        if "is_starred" in validated:
            fs.is_starred = bool(validated["is_starred"])
            updates.append("is_starred")
        if validated.get("is_default_for_user") is True:
            FilterSet.objects.filter(
                team=request.team, user=request.user, table_type=fs.table_type, is_default_for_user=True
            ).exclude(id=fs.id).update(is_default_for_user=False)
            fs.is_default_for_user = True
            updates.append("is_default_for_user")
        elif validated.get("is_default_for_user") is False:
            fs.is_default_for_user = False
            updates.append("is_default_for_user")
        if validated.get("is_default_for_team") is True:
            FilterSet.objects.filter(
                team=request.team, table_type=fs.table_type, is_default_for_team=True
            ).exclude(id=fs.id).update(is_default_for_team=False)
            fs.is_default_for_team = True
            updates.append("is_default_for_team")
        elif validated.get("is_default_for_team") is False:
            fs.is_default_for_team = False
            updates.append("is_default_for_team")

        if updates:
            fs.save(update_fields=updates)

    return JsonResponse({"result": _serialize_filter_set(fs)})
