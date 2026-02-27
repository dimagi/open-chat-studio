from collections import Counter
from collections.abc import Generator
from functools import reduce
from operator import or_
from typing import Any, Literal

from django.conf import settings
from django.contrib.admin.utils import NestedObjects
from django.core.mail import send_mail
from django.db import models, router, transaction
from django.db.models import Expression, Q
from django.db.models.deletion import get_candidate_relations_to_delete
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from field_audit import field_audit
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
            _queryset_update_with_auditing(combined_updates, **{field.name: value})
        if objs:
            model = objs[0].__class__
            objects_filter = model.objects.filter(list({obj.pk for obj in objs}))
            objects_filter.update(**{field.name: value}, audit_action=AuditAction.AUDIT)


def _queryset_update_with_auditing(queryset, **kw):
    """
    Copied from `field_audit.models.AuditingQuerySet.update` so that it can be called with querysets
    that are not AuditingQuerySets.
    """
    from field_audit import AuditService
    from field_audit.models import AuditEvent

    audit_service = AuditService()
    fields_to_update = set(kw.keys())
    audited_fields = set(audit_service.get_field_names(queryset.model))
    fields_to_audit = fields_to_update & audited_fields
    if not fields_to_audit:
        # no audited fields are changing
        return queryset.update(**kw)

    new_values = {field: kw[field] for field in fields_to_audit}
    uses_expressions = any([isinstance(val, Expression) for val in new_values.values()])

    old_values = {}
    values_to_fetch = fields_to_update | {"pk"}
    for value in queryset.values(*values_to_fetch):
        pk = value.pop("pk")
        old_values[pk] = value

    with transaction.atomic(using=queryset.db):
        rows = queryset.update(**kw)
        if uses_expressions:
            # fetch updated values to ensure audit event deltas are accurate
            # after update is performed with expressions
            new_values = {}
            for value in queryset.values(*values_to_fetch):
                pk = value.pop("pk")
                new_values[pk] = value
        else:
            new_values = {pk: new_values for pk in old_values}

        # create and write the audit events _after_ the update succeeds
        request = field_audit.request.get()
        audit_events = []
        for pk, old_values_for_pk in old_values.items():
            audit_event = audit_service.make_audit_event_from_values(
                old_values_for_pk, new_values[pk], pk, queryset.model, request
            )
            if audit_event:
                audit_events.append(audit_event)
        if audit_events:
            AuditEvent.objects.bulk_create(audit_events)
        return rows


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
        qs = collector.related_objects(through_model, [field], objs)  # ty: ignore[invalid-argument-type]
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


def get_related_objects(instance, pipeline_param_key: str | None = None) -> list:
    from apps.pipelines.models import Node

    related_objects = []

    for queryset in _get_related_objects_querysets(instance, pipeline_param_key):
        if queryset.model == Node:
            related_objects.extend([node.pipeline for node in queryset.only("pipeline").all()])
        else:
            related_objects.extend(queryset.all())

    return related_objects


def has_related_objects(instance, pipeline_param_key: str | None = None) -> bool:
    return any(queryset.exists() for queryset in _get_related_objects_querysets(instance, pipeline_param_key))


def _get_related_objects_querysets(instance, pipeline_param_key: str | None = None) -> Generator[Any | None, Any]:
    for related in get_candidate_relations_to_delete(instance._meta):
        related_objects = getattr(instance, related.get_accessor_name(), None)
        if related_objects is not None:
            yield related_objects

    if pipeline_param_key:
        yield get_related_pipelines_queryset(instance, pipeline_param_key)


def get_related_pipelines_queryset(instance, pipeline_param_key: str | None = None):
    from apps.pipelines.models import Node

    pipelines = Node.objects.filter(
        Q(**{f"params__{pipeline_param_key}": instance.id}) | Q(**{f"params__{pipeline_param_key}": str(instance.id)})
    )
    return pipelines


def get_related_pipelines_queryset_for_list_param(instance, pipeline_param_key: str | None = None):
    from apps.pipelines.models import Node

    pipelines = Node.objects.filter(
        Q(**{f"params__{pipeline_param_key}__contains": instance.id})
        | Q(**{f"params__{pipeline_param_key}__contains": str(instance.id)})
    )
    return pipelines


def get_related_pipeline_experiments_queryset(instance_ids, pipeline_param_key: str):
    """Get all experiments that reference any id in `instance_ids`, located at the `pipeline_param_key`
    parameter"""
    return _get_related_pipeline_experiments_queryset(instance_ids, pipeline_param_key, "__in")


def get_related_pipeline_experiments_queryset_list_param(instance_ids, pipeline_param_key: str):
    """Get all experiments that reference any id in `instance_ids`, located at the `pipeline_param_key`
    parameter where the param value is a list."""
    return _get_related_pipeline_experiments_queryset(instance_ids, pipeline_param_key, "__contains")


def _get_related_pipeline_experiments_queryset(
    instance_ids, pipeline_param_key: str, operator: Literal["__in", "__contains"]
):
    from apps.experiments.models import Experiment

    instance_ids_str = [str(instance_id) for instance_id in instance_ids]
    instance_ids_int = [int(instance_id) for instance_id in instance_ids]
    return (
        Experiment.objects.exclude(pipeline=None)
        .filter(
            Q(**{f"pipeline__node__params__{pipeline_param_key}{operator}": instance_ids_int})
            | Q(**{f"pipeline__node__params__{pipeline_param_key}{operator}": instance_ids_str})
        )
        .distinct()
    )


def get_admin_emails_with_delete_permission(team):
    from apps.teams.models import Membership

    return list(
        Membership.objects.filter(team__name=team.name, groups__permissions__codename="delete_team").values_list(
            "user__email", flat=True
        )
    )


def send_team_deleted_notification(team_name, admin_emails):
    email_context = {
        "team_name": team_name,
    }
    send_mail(
        subject=_("Team '{}' has been deleted").format(team_name),
        message=render_to_string("teams/email/team_deleted_notification.txt", context=email_context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=admin_emails,
        fail_silently=False,
        html_message=render_to_string("teams/email/team_deleted_notification.html", context=email_context),
    )


def chunk_list(lst, chunk_size):
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]
