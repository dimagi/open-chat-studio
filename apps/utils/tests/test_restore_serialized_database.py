import json

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection

from apps.utils.pytest import restore_serialized_database


@pytest.mark.django_db()
def test_restore_overwrites_a_row_that_came_back_under_a_new_primary_key():
    """Replaying the snapshot must replace a content type that came back under a new id.

    A `django_db(transaction=True)` test flushes the database, and the `post_migrate` that follows
    re-creates every content type with a fresh id, while the session-start snapshot still holds the
    old one.
    """
    ContentType.objects.clear_cache()
    original = ContentType.objects.get(app_label="contenttypes", model="contenttype")
    original_pk = original.pk
    snapshot = json.dumps(
        [
            {
                "model": "contenttypes.contenttype",
                "pk": original_pk,
                "fields": {"app_label": original.app_label, "model": original.model},
            }
        ]
    )

    original.delete()
    renumbered = ContentType.objects.create(app_label="contenttypes", model="contenttype")
    assert renumbered.pk != original_pk
    # Fire the deferred foreign key triggers the delete queued, or the TRUNCATE below is refused.
    connection.check_constraints()

    restore_serialized_database(connection, snapshot)

    ContentType.objects.clear_cache()
    assert ContentType.objects.get(app_label="contenttypes", model="contenttype").pk == original_pk
