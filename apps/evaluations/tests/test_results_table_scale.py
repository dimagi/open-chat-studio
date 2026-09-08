"""Scale regression for the results table/detail-panel data path.

`EvaluationResultDataMixin.get_table_data()` filters the run's aggregated rows in
Python, then gets called multiple times per request: django-tables2's own
`SingleTableMixin.get_table()` calls it once directly and our `get_table_class()`
calls it again to build columns, and the detail view's prev/next calls it once per
click. Without memoizing the built row list (see `_table_rows`), each of those calls
re-runs the full per-message DB query and Python-side aggregation from scratch - fine
at a handful of rows, but a real cost once a run has 1000+ results.

These tests build a 1200-row run and compare its query count against a tiny run's,
rather than pinning an absolute number: this app's shared chrome (nav, permissions,
notifications) issues a handful of queries per *rendered row* that have nothing to do
with this feature, so an absolute ceiling would either be too tight (flaky on
unrelated template changes) or too loose (blind to a real regression). Asserting the
delta stays small directly encodes "doesn't scale with dataset size, only with the
fixed page size" - what a redundant-rebuild or an N+1 in the underlying query would
actually break.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.evaluations.models import EvaluationMessage, EvaluationResult
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)

_LARGE_ROW_COUNT = 1200
_SMALL_ROW_COUNT = 10
# However many extra queries the page-size-bounded chrome (pagination widget, per-row
# permission checks, etc.) can reasonably add - not something this feature controls -
# without it, one real N+1 across 1200 rows would still blow past this by 10-100x.
_QUERY_COUNT_SLACK = 15


def _seed_run(team, evaluator, run, *, row_count):
    messages = EvaluationMessage.objects.bulk_create(
        [
            EvaluationMessage(
                input={"content": f"input {i}", "role": "human"},
                output={"content": f"output {i}", "role": "ai"},
            )
            for i in range(row_count)
        ]
    )
    EvaluationResult.objects.bulk_create(
        [
            EvaluationResult(
                team=team,
                run=run,
                evaluator=evaluator,
                message=message,
                output={
                    "message": {
                        "input": message.input,
                        "output": {"content": f"generated {i}", "role": "ai"},
                        "context": {},
                    },
                    "result": {"acceptability": "Acceptable" if i % 2 == 0 else "Unacceptable"},
                    "generated_response": f"generated {i}",
                },
            )
            for i, message in enumerate(messages)
        ]
    )
    return messages


def _make_run(team, *, row_count):
    evaluator = EvaluatorFactory.create(
        team=team,
        name="Acceptability Judge",
        params={
            "llm_prompt": "x",
            "output_schema": {
                "acceptability": {"type": "choice", "description": "x", "choices": ["Acceptable", "Unacceptable"]},
            },
        },
    )
    config = EvaluationConfigFactory.create(team=team, evaluators=[evaluator])
    run = EvaluationRunFactory.create(team=team, config=config, evaluator_ids=[evaluator.id])
    messages = _seed_run(team, evaluator, run, row_count=row_count)
    return evaluator, config, run, messages


@pytest.mark.django_db()
class TestResultsTableAtScale:
    def test_filtering_a_1200_row_run_returns_correct_rows_with_bounded_queries(self, client, team_with_users):
        small_evaluator, small_config, small_run, _ = _make_run(team_with_users, row_count=_SMALL_ROW_COUNT)
        large_evaluator, large_config, large_run, _ = _make_run(team_with_users, row_count=_LARGE_ROW_COUNT)
        client.force_login(team_with_users.members.first())

        def _get(config, run, evaluator):
            url = reverse("evaluations:evaluation_results_table", args=[team_with_users.slug, config.id, run.id])
            with CaptureQueriesContext(connection) as ctx:
                response = client.get(
                    url, {"filter_field": f"acceptability ({evaluator.name})", "filter_value": "Acceptable"}
                )
            return response, len(ctx.captured_queries)

        small_response, small_queries = _get(small_config, small_run, small_evaluator)
        large_response, large_queries = _get(large_config, large_run, large_evaluator)

        assert small_response.status_code == 200
        assert large_response.status_code == 200
        assert large_queries <= small_queries + _QUERY_COUNT_SLACK, (
            f"query count grew from {small_queries} ({_SMALL_ROW_COUNT} rows) to "
            f"{large_queries} ({_LARGE_ROW_COUNT} rows) - looks like it's scaling with "
            "dataset size rather than staying bounded by page size"
        )

        table = large_response.context["table"]
        assert len(table.page.object_list) == 10  # one page, per table_pagination
        assert table.paginator.count == _LARGE_ROW_COUNT // 2

    def test_detail_panel_prev_next_over_a_1200_row_run_has_bounded_queries(self, client, team_with_users):
        small_evaluator, small_config, small_run, small_messages = _make_run(team_with_users, row_count=3)
        _, large_config, large_run, large_messages = _make_run(team_with_users, row_count=_LARGE_ROW_COUNT)
        client.force_login(team_with_users.members.first())

        def _get(config, run, message_id):
            url = reverse(
                "evaluations:evaluation_result_detail",
                args=[team_with_users.slug, config.id, run.id, message_id],
            )
            with CaptureQueriesContext(connection) as ctx:
                response = client.get(url)
            return response, len(ctx.captured_queries)

        small_response, small_queries = _get(small_config, small_run, small_messages[1].id)
        large_response, large_queries = _get(large_config, large_run, large_messages[600].id)

        assert small_response.status_code == 200
        assert large_response.status_code == 200
        assert large_queries <= small_queries + _QUERY_COUNT_SLACK, (
            f"query count grew from {small_queries} (3 rows) to {large_queries} "
            f"({_LARGE_ROW_COUNT} rows) opening a single result's detail panel - looks "
            "like the whole run's row list is being rebuilt more than once per request"
        )

        content = large_response.content.decode()
        assert "generated 600" in content
        # Both directions are reachable - confirms the row list built for one detail
        # lookup covers the whole (memoized) run, not just a page-sized slice.
        assert f"/{large_messages[599].id}/detail" in content
        assert f"/{large_messages[601].id}/detail" in content
