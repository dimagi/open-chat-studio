import json
from datetime import datetime

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q

from apps.annotations.models import CustomTaggedItem
from apps.chat.models import Chat, ChatMessage


def apply_dynamic_filters(query_set, request, parsed_params=None):
    param_source = parsed_params if parsed_params is not None else request.GET
    filter_conditions = Q()
    filter_applied = False

    for i in range(30):
        filter_column = param_source.get(f"filter_{i}_column")
        filter_operator = param_source.get(f"filter_{i}_operator")
        filter_value = param_source.get(f"filter_{i}_value")

        if not all([filter_column, filter_operator, filter_value]):
            break

        filter_column = filter_column[0] if isinstance(filter_column, list) else filter_column
        filter_operator = filter_operator[0] if isinstance(filter_operator, list) else filter_operator
        filter_value = filter_value[0] if isinstance(filter_value, list) else filter_value

        condition = build_filter_condition(filter_column, filter_operator, filter_value)
        if condition:
            filter_conditions &= condition
            filter_applied = True

    if filter_applied:
        query_set = query_set.filter(filter_conditions).distinct()

    return query_set


def build_filter_condition(column, operator, value):
    if not value:
        return None
    if column == "participant":
        return build_participant_filter(operator, value)
    elif column == "last_message":
        return build_timestamp_filter(operator, value)
    elif column == "tags":
        return build_tags_filter(operator, value)
    elif column == "versions":
        return build_versions_filter(operator, value)
    return None


def build_participant_filter(operator, value):
    """Build filter condition for participant"""
    if operator == "equals":
        return Q(participant__identifier=value)
    elif operator == "contains":
        return Q(participant__identifier__icontains=value)
    elif operator == "does not contain":
        return ~Q(participant__identifier__icontains=value)
    elif operator == "starts_with":
        return Q(participant__identifier__istartswith=value)
    elif operator == "ends_with":
        return Q(participant__identifier__iendswith=value)
    return None


def build_timestamp_filter(operator, value):
    """Build filter condition for timestamp"""
    try:
        date_value = datetime.strptime(value, "%Y-%m-%d").date()
        if operator == "on":
            return Q(last_message_created_at__date=date_value)
        elif operator == "before":
            return Q(last_message_created_at__date__lt=date_value)
        elif operator == "after":
            return Q(last_message_created_at__date__gt=date_value)
    except (ValueError, TypeError):
        pass
    return None


def build_tags_filter(operator, value):
    try:
        selected_tags = json.loads(value)
        if not selected_tags:
            return None
        if operator == "any of":
            return Q(chat__tags__name__in=selected_tags)
        elif operator == "all of":
            content_type = ContentType.objects.get_for_model(Chat)
            conditions = Q()
            for tag in selected_tags:
                conditions &= Exists(
                    CustomTaggedItem.objects.filter(
                        object_id=OuterRef("id"),
                        content_type_id=content_type,
                        tag__name=tag,
                    )
                )
            return conditions
    except json.JSONDecodeError:
        pass
    return None


def build_versions_filter(operator, value):
    try:
        version_strings = json.loads(value)
        if not version_strings:
            return None
        version_tags = [v for v in version_strings if v]
        if operator == "any of":
            tag_exists = [
                ChatMessage.objects.filter(
                    chat=OuterRef("chat"),
                    tags__name__startswith=tag,
                    tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
                ).values("id")
                for tag in version_tags
            ]
            combined_query = Q()
            for query in tag_exists:
                combined_query |= Q(Exists(query))

            return combined_query

        elif operator == "all of":
            q_objects = Q()
            for tag in version_tags:
                tag_exists = ChatMessage.objects.filter(
                    chat=OuterRef("chat"),
                    tags__name__startswith=tag,
                    tags__category=Chat.MetadataKeys.EXPERIMENT_VERSION,
                ).values("id")
                q_objects &= Q(Exists(tag_exists))
            return q_objects
    except json.JSONDecodeError:
        pass
    return None
