from collections import Counter
from functools import reduce
from operator import or_
from typing import Any

from django.contrib.admin.utils import NestedObjects
from django.db import models, router, transaction
from django.db.models.deletion import get_candidate_relations_to_delete
from field_audit.field_audit import get_audited_models


def delete_object_with_auditing_of_related_objects(obj):
    """Deletes the given object and its related objects, auditing the deletion of each object.

    Args:
        obj: The object to delete.
    """
    from field_audit.models import AuditAction, AuditingManager

    collector = NestedObjects(using="default")
    collector.collect([obj])

    models_to_delete = set(collector.data)

    updates_not_part_of_delete = {}
    if collector.field_updates:
        for (field, value), instances_list in collector.field_updates.items():
            model = field.model
            if model in models_to_delete or model._meta.auto_created or model not in get_audited_models():
                continue
            updates_not_part_of_delete[(field, value)] = instances_list

    counter = Counter()
    with transaction.atomic():
        _perform_updates_for_delete(updates_not_part_of_delete)

        for model, instances in reversed(collector.data.items()):
            if model._meta.auto_created:
                continue

            if len(instances) == 1:
                list(instances)[0].delete()
                counter[model._meta.label] += 1
                continue

            audit_kwargs = {}
            if model in get_audited_models() and isinstance(model._default_manager, AuditingManager):
                audit_kwargs["audit_action"] = AuditAction.AUDIT

            _, stats = model.objects.filter(pk__in=[instance.pk for instance in instances]).delete(**audit_kwargs)
            counter.update(stats)
    return dict(counter)


def _perform_updates_for_delete(updates_not_part_of_delete):
    """Copied from django.db.models.deletion.Collector.delete()
    to perform updates for objects that are not getting deleted but are affected by the delete operation.
    e.g. cascading updates to related objects that are not being deleted."""
    from field_audit.models import AuditAction

    for (field, value), instances_list in updates_not_part_of_delete.items():
        updates = []
        objs = []
        for instances in instances_list:
            if isinstance(instances, models.QuerySet) and instances._result_cache is None:
                updates.append(instances)
            else:
                objs.extend(instances)
        if updates:
            combined_updates = reduce(or_, updates)

            # hack to give the queryset the auditing update method
            combined_updates.update = field.model.objects.update
            combined_updates.update(**{field.name: value}, audit_action=AuditAction.AUDIT)
        if objs:
            model = objs[0].__class__
            objects_filter = model.objects.filter(list({obj.pk for obj in objs}))
            objects_filter.update(**{field.name: value}, audit_action=AuditAction.AUDIT)


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
