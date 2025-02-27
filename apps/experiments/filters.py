import json
from datetime import datetime

from django.db.models import Exists, OuterRef, Q

from apps.chat.models import Chat, ChatMessage


def build_participant_filter(operator, value):
    """Build filter condition for participant"""
    if operator == "equals":
        return Q(participant__identifier=value)
    elif operator == "contains":
        return Q(participant__identifier__icontains=value)
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
            conditions = Q()
            for tag in selected_tags:
                conditions &= Q(chat__tags__name=tag)
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
                ).values("id")[:1]
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
                ).values("id")[:1]
                q_objects &= Q(Exists(tag_exists))
            return q_objects
    except json.JSONDecodeError:
        pass
    return None
