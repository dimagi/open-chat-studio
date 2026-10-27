"""Tests for EvaluationResultDetailView: the side panel a results-table row opens into."""

import re

import pytest
from django.urls import reverse

from apps.evaluations.evaluators import EvaluatorResult
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory


def _output(*, sentiment="positive", input_content="hi", generated="hi there", judged=None) -> dict:
    return EvaluatorResult(
        message={
            "input": {"content": input_content, "role": "human"},
            "output": {"content": "expected output", "role": "ai"},
            "context": {},
            "history": [],
            "metadata": {},
        },
        result={"sentiment": sentiment},
        generated_response=generated,
        judged_response=judged,
    ).model_dump()


def _result(run, evaluator, output=None, **kwargs):
    return EvaluationResultFactory.create(
        output=output or _output(), team=run.team, run=run, evaluator=evaluator, **kwargs
    )


def _detail_url(team, config, run, message_id):
    return reverse("evaluations:evaluation_result_detail", args=[team.slug, config.id, run.id, message_id])


@pytest.mark.django_db()
class TestEvaluationResultDetailView:
    def test_renders_dataset_and_generated_response_fields(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator, output=_output(input_content="What happened?", generated="It went well."))
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        assert response.status_code == 200
        content = response.content.decode()
        assert "What happened?" in content
        assert "It went well." in content
        assert "expected output" in content
        assert "#1" in content

    def test_shows_the_judged_span_text_beside_the_generated_response(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator, output=_output(generated="hi there", judged="tagged: hello"))
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        content = response.content.decode()
        assert "hi there" in content
        assert "tagged: hello" in content
        assert "JUDGED RESPONSE (Judge)" in content

    def test_shows_a_dash_when_the_judged_span_text_is_empty(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator, output=_output(judged=""))
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        content = response.content.decode()
        assert content.count('data-testid="result-detail-judged-response"') == 1
        assert "JUDGED RESPONSE (Judge)" in content
        assert re.search(r'data-testid="result-detail-judged-response">\s*—\s*</div>', content)

    def test_escapes_the_judged_span_text(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator, output=_output(judged='<b>tagged</b> & "quoted"'))
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        content = response.content.decode()
        assert "&lt;b&gt;tagged&lt;/b&gt; &amp; &quot;quoted&quot;" in content
        assert "<b>tagged</b>" not in content

    def test_no_judged_response_block_when_the_evaluator_judged_the_reply(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        assert "JUDGED RESPONSE" not in response.content.decode()

    def test_only_the_span_evaluator_gets_a_judged_response_block(self, client, team_with_users):
        span_evaluator = EvaluatorFactory.create(team=team_with_users, name="Span Judge")
        reply_evaluator = EvaluatorFactory.create(team=team_with_users, name="Reply Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[span_evaluator, reply_evaluator])
        run = EvaluationRunFactory.create(
            team=team_with_users, config=config, evaluator_ids=[span_evaluator.id, reply_evaluator.id]
        )
        result = _result(run, span_evaluator, output=_output(judged="tagged: hello"))
        _result(run, reply_evaluator, message=result.message)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        content = response.content.decode()
        assert content.count('data-testid="result-detail-judged-response"') == 1
        assert "JUDGED RESPONSE (Span Judge)" in content
        assert "JUDGED RESPONSE (Reply Judge)" not in content

    def test_panel_carries_its_message_id_for_the_table_row_highlight_sync(self, client, team_with_users):
        """The panel root's data-result-id drives `syncEvalResultHighlight()` (see
        evaluation_result_home.html), which highlights the table row matching whichever
        result the panel currently shows."""
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        assert f'data-result-id="{result.message_id}"' in response.content.decode()

    def test_shows_badge_for_categorical_field(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(
            team=team_with_users,
            name="Acceptability Judge",
            params={
                "llm_prompt": "x",
                "output_schema": {
                    "acceptability": {
                        "type": "choice",
                        "description": "x",
                        "choices": ["Acceptable", "Unacceptable"],
                    },
                },
            },
        )
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        output = EvaluatorResult(
            message={
                "input": {"content": "hi", "role": "human"},
                "output": {"content": "hi", "role": "ai"},
                "context": {},
                "history": [],
                "metadata": {},
            },
            result={"acceptability": "Acceptable"},
            generated_response="hi",
        ).model_dump()
        result = EvaluationResultFactory.create(output=output, team=team_with_users, run=run, evaluator=evaluator)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        assert response.status_code == 200
        assert "Acceptability: Acceptable" in response.content.decode()

    def test_prev_and_next_navigate_in_row_order(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        first = _result(run, evaluator, output=_output(sentiment="positive"))
        middle = _result(run, evaluator, output=_output(sentiment="neutral"))
        last = _result(run, evaluator, output=_output(sentiment="negative"))
        client.force_login(team_with_users.members.first())

        first_resp = client.get(_detail_url(team_with_users, config, run, first.message_id)).content.decode()
        middle_resp = client.get(_detail_url(team_with_users, config, run, middle.message_id)).content.decode()
        last_resp = client.get(_detail_url(team_with_users, config, run, last.message_id)).content.decode()

        middle_url = _detail_url(team_with_users, config, run, middle.message_id)
        last_url = _detail_url(team_with_users, config, run, last.message_id)
        first_url = _detail_url(team_with_users, config, run, first.message_id)

        assert middle_url in first_resp  # first result can only navigate forward
        assert first_url not in first_resp

        assert first_url in middle_resp
        assert last_url in middle_resp

        assert middle_url in last_resp  # last result can only navigate backward
        assert last_url not in last_resp

    def test_view_session_link_present_when_result_has_a_session(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        session = ExperimentSessionFactory.create(team=team_with_users)
        result = _result(run, evaluator, session=session)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        expected_url = reverse(
            "chatbots:chatbot_session_view",
            args=[team_with_users.slug, session.experiment.public_id, session.external_id],
        )
        assert expected_url in response.content.decode()

    def test_view_session_link_falls_back_to_the_messages_source_session(self, client, team_with_users):
        """Most runs are message-mode with no generation, so `result.session` (this run's
        own session) is never set - but the dataset message can still carry the source
        session it was imported from (`message.session`), which is what "View session"
        should actually link to in that common case."""
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        source_session = ExperimentSessionFactory.create(team=team_with_users)
        message = EvaluationMessageFactory.create(session=source_session)
        result = _result(run, evaluator, message=message)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        expected_url = reverse(
            "chatbots:chatbot_session_view",
            args=[team_with_users.slug, source_session.experiment.public_id, source_session.external_id],
        )
        assert expected_url in response.content.decode()

    def test_view_session_link_absent_without_a_session(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        assert "View session" not in response.content.decode()

    def test_unknown_message_id_404s(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        _result(run, evaluator)
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, 999999))

        assert response.status_code == 404

    def test_tokens_and_cost_shown(self, client, team_with_users):
        evaluator = EvaluatorFactory.create(team=team_with_users, name="Judge")
        config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team_with_users, config=config, evaluator_ids=[evaluator.id])
        result = _result(run, evaluator)
        UsageRecordFactory.create(
            team=team_with_users,
            evaluation_config=config,
            quantity=100,
            extra={"evaluation_run_id": run.id, "message_id": result.message_id},
        )
        client.force_login(team_with_users.members.first())

        response = client.get(_detail_url(team_with_users, config, run, result.message_id))

        assert response.status_code == 200
        assert "100" in response.content.decode()
