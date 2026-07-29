from unittest.mock import patch

import pytest
from django.db import connection
from django.urls import reverse

from apps.filters.models import FilterSet


@pytest.mark.django_db()
class TestCreateFilterSet:
    def _url(self, team, table_type=FilterSet.TableType.SESSIONS):
        return reverse("filters:create_filter_set", args=[team.slug, table_type])

    def test_create_filter_set(self, client, team_with_users):
        user = team_with_users.members.first()
        client.force_login(user)

        response = client.post(
            self._url(team_with_users),
            data={"name": "My filter", "filter_query_string": "filter_0_column=status"},
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert FilterSet.objects.filter(team=team_with_users, name="My filter").exists()

    def test_integrity_error_returns_400_and_leaves_transaction_usable(self, client, team_with_users):
        """A DB error while saving must return a 400, not blow up on the way out of the atomic block.

        Regression test: the save used to sit inside `transaction.atomic()` with the
        `except IntegrityError` *inside* the block, so the atomic block exited "cleanly" on a
        transaction Postgres had already aborted. Committing / releasing the savepoint then failed
        with "current transaction is aborted" (or, if anything else queried first, with
        "An error occurred in the current transaction. You can't execute queries until the end of
        the 'atomic' block").
        """
        user = team_with_users.members.first()
        client.force_login(user)
        existing = FilterSet.objects.create(
            team=team_with_users,
            user=user,
            table_type=FilterSet.TableType.SESSIONS,
            name="Existing",
            filter_query_string="filter_0_column=status",
        )

        def duplicate_key_insert(*args, **kwargs):
            # Raw SQL so the failure is a plain aborted transaction, the way it arrives from
            # paths Django doesn't wrap in `mark_for_rollback_on_error` (raw SQL, deferred
            # constraints, multi-query saves).
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO filters_filterset (id, name, table_type, filter_query_string, is_shared,"
                    " is_starred, is_default_for_user, is_default_for_team, team_id, user_id, created_at,"
                    " updated_at) VALUES (%s, 'dup', 'sessions', 'a=1', false, false, false, false, %s, %s,"
                    " now(), now())",
                    [existing.id, team_with_users.id, user.id],
                )

        with patch("apps.filters.serializers.FilterSetSerializer.save", side_effect=duplicate_key_insert):
            response = client.post(
                self._url(team_with_users),
                data={"name": "My filter", "filter_query_string": "filter_0_column=status"},
            )

        assert response.status_code == 400
        assert response.json() == {"error": "Unable to create filter set"}
        # The failed save was rolled back cleanly, so the connection is still usable.
        assert FilterSet.objects.filter(team=team_with_users).count() == 1
