from typing import Any

from django.contrib.admin.utils import NestedObjects
from django.db import router
from django.db.models.deletion import get_candidate_relations_to_delete


def get_related_m2m_objects(objs, exclude: list | None = None) -> dict[Any, list[Any]]:
    """Returns a set of objects related to the given objects through many-to-many relationships.

    Args:
        objs (list): A list of objects to find related objects for.
        exclude (list): A list of objects to exclude from the result.
    """
    related_mapping = {}
    try:
        obj = objs[0]
    except IndexError:
        return related_mapping

    using = router.db_for_write(obj._meta.model)
    model = obj.__class__
    collector = NestedObjects(using=using, origin=objs)
    m2m_models = _get_m2m_related_models(model)
    for related in get_candidate_relations_to_delete(model._meta):
        if related.many_to_many:
            through_model = related.related_model
        else:
            assert related.one_to_many
            through_model = related.related_model

        related_model = m2m_models.get(through_model)
        if not related_model:
            # relation is the reverse side of a normal FK
            continue

        # get the other side of the relationship (the one that is not the origin)
        related_field = [
            f for f in through_model._meta.get_fields() if f.is_relation and f.related_model == related_model
        ][0]

        field = related.field
        qs = collector.related_objects(through_model, [field], objs)
        if exclude:
            exclude_instances = [instance for instance in exclude if isinstance(instance, related_model)]
            qs = qs.exclude(**{f"{related_field.name}__in": exclude_instances})
        qs = qs.select_related(related_field.name)
        for related_obj in qs:
            target_obj = getattr(related_obj, related_field.name)
            source_obj = getattr(related_obj, field.name)
            related_mapping.setdefault(source_obj, set()).add(target_obj)

    return related_mapping


def _get_m2m_related_models(model):
    m2m_models = {}
    for field in model._meta.get_fields():
        if field.many_to_many:
            through_model = field.through if hasattr(field, "through") else field.remote_field.through
            m2m_models[through_model] = field.related_model
    return m2m_models
