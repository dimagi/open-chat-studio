"""Tests for the "Clear all" action: un-apply eval tags and delete all runs for a config."""

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.annotations.models import CustomTaggedItem
from apps.evaluations.models import (
    AppliedTag,
    ConditionType,
    EvaluationMode,
    EvaluationResult,
    EvaluationRun,
)
from apps.evaluations.tagging import remove_applied_tags_for_runs
from apps.utils.factories.evaluations import (
    AppliedTagFactory,
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluationTagFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import GroupFactory, UserFactory


@pytest.fixture()
def team(db):
    return TeamFactory.create()


@pytest.fixture()
def evaluator(team):
    return EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)


@pytest.fixture()
def tag_a(team):
    return EvaluationTagFactory.create(team=team, name="tag-a")


@pytest.fixture()
def tag_rule(team, evaluator, tag_a):
    return EvaluatorTagRuleFactory.create(
        team=team,
        evaluator=evaluator,
        tag=tag_a,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "negative"},
    )


def _build_run(team, evaluator, messages, evaluation_mode=EvaluationMode.MESSAGE):
    dataset = EvaluationDatasetFactory.create(team=team, messages=messages, evaluation_mode=evaluation_mode)
    config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
    return EvaluationRunFactory.create(team=team, config=config)


class TestRemoveAppliedTagsForRuns:
    def test_removes_eval_applied_tag_from_message_target(self, team, evaluator, tag_a, tag_rule):
        """An eval-applied tag is removed from the message target."""
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message
        chat_message.tags.add(tag_a, through_defaults={"team": team})

        run = _build_run(team, evaluator, [message])
        result = EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})
        AppliedTagFactory.create(team=team, evaluation_result=result, rule=tag_rule, tag=tag_a)

        remove_applied_tags_for_runs(EvaluationRun.objects.filter(pk=run.pk))

        assert not chat_message.tags.filter(pk=tag_a.pk).exists()

    def test_removes_eval_applied_tag_from_session_chat_target(self, team, tag_a):
        """Session-mode evaluators target the Chat; the applied tag is removed from it."""
        session_evaluator = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.SESSION)
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=session_evaluator,
            tag=tag_a,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        session = ExperimentSessionFactory.create(team=team)
        message = EvaluationMessageFactory.create(session=session)
        chat = session.chat
        chat.tags.add(tag_a, through_defaults={"team": team})

        run = _build_run(team, session_evaluator, [message], evaluation_mode=EvaluationMode.SESSION)
        result = EvaluationResultFactory.create(
            team=team, evaluator=session_evaluator, message=message, run=run, output={}
        )
        AppliedTagFactory.create(team=team, evaluation_result=result, rule=rule, tag=tag_a)

        remove_applied_tags_for_runs(EvaluationRun.objects.filter(pk=run.pk))

        assert not chat.tags.filter(pk=tag_a.pk).exists()

    def test_preserves_manually_added_tag(self, team, evaluator, tag_a, tag_rule):
        """A tag added by a person (user set) is preserved even when in the eval audit."""
        user = UserFactory.create()
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message
        chat_message.add_tag(tag_a, team=team, added_by=user)

        run = _build_run(team, evaluator, [message])
        result = EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})
        AppliedTagFactory.create(team=team, evaluation_result=result, rule=tag_rule, tag=tag_a)

        remove_applied_tags_for_runs(EvaluationRun.objects.filter(pk=run.pk))

        assert chat_message.tags.filter(pk=tag_a.pk).exists()

    def test_aggregates_across_multiple_runs(self, team, evaluator, tag_a, tag_rule):
        """Tags applied across several runs of the config are all removed."""
        message_one = EvaluationMessageFactory.create(create_chat_messages=True)
        message_two = EvaluationMessageFactory.create(create_chat_messages=True)
        message_one.expected_output_chat_message.tags.add(tag_a, through_defaults={"team": team})
        message_two.expected_output_chat_message.tags.add(tag_a, through_defaults={"team": team})

        run = _build_run(team, evaluator, [message_one, message_two])
        run_two = EvaluationRunFactory.create(team=team, config=run.config)
        result_one = EvaluationResultFactory.create(
            team=team, evaluator=evaluator, message=message_one, run=run, output={}
        )
        result_two = EvaluationResultFactory.create(
            team=team, evaluator=evaluator, message=message_two, run=run_two, output={}
        )
        AppliedTagFactory.create(team=team, evaluation_result=result_one, rule=tag_rule, tag=tag_a)
        AppliedTagFactory.create(team=team, evaluation_result=result_two, rule=tag_rule, tag=tag_a)

        remove_applied_tags_for_runs(EvaluationRun.objects.filter(config=run.config))

        assert not message_one.expected_output_chat_message.tags.filter(pk=tag_a.pk).exists()
        assert not message_two.expected_output_chat_message.tags.filter(pk=tag_a.pk).exists()

    def test_tag_without_audit_untouched(self, team, evaluator, tag_a, tag_rule):
        """A tag on a target with no AppliedTag row is left in place."""
        tag_b = EvaluationTagFactory.create(team=team, name="tag-b-no-audit")
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message
        chat_message.tags.add(tag_b, through_defaults={"team": team})

        run = _build_run(team, evaluator, [message])
        EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})

        remove_applied_tags_for_runs(EvaluationRun.objects.filter(pk=run.pk))

        assert chat_message.tags.filter(pk=tag_b.pk).exists()

    def test_none_target_skipped_gracefully(self, team, evaluator, tag_rule, tag_a):
        """An AppliedTag whose result resolves to no target is skipped without error."""
        message = EvaluationMessageFactory.create()  # no chat messages → target is None
        run = _build_run(team, evaluator, [message])
        result = EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})
        AppliedTagFactory.create(team=team, evaluation_result=result, rule=tag_rule, tag=tag_a)

        remove_applied_tags_for_runs(EvaluationRun.objects.filter(pk=run.pk))  # must not raise

        assert CustomTaggedItem.objects.count() == 0


class TestClearEvaluationRunsView:
    def _setup_config_with_applied_tag(self, team, user=None):
        evaluator = EvaluatorFactory.create(team=team, evaluation_mode=EvaluationMode.MESSAGE)
        tag = EvaluationTagFactory.create(team=team, name="applied")
        rule = EvaluatorTagRuleFactory.create(
            team=team,
            evaluator=evaluator,
            tag=tag,
            field_name="sentiment",
            condition_type=ConditionType.EQUALS,
            condition_value={"value": "negative"},
        )
        message = EvaluationMessageFactory.create(create_chat_messages=True)
        chat_message = message.expected_output_chat_message
        if user is not None:
            chat_message.add_tag(tag, team=team, added_by=user)
        else:
            chat_message.tags.add(tag, through_defaults={"team": team})

        dataset = EvaluationDatasetFactory.create(team=team, messages=[message])
        config = EvaluationConfigFactory.create(team=team, dataset=dataset, evaluators=[evaluator])
        run = EvaluationRunFactory.create(team=team, config=config)
        result = EvaluationResultFactory.create(team=team, evaluator=evaluator, message=message, run=run, output={})
        AppliedTagFactory.create(team=team, evaluation_result=result, rule=rule, tag=tag)
        return config, chat_message, tag

    @pytest.mark.django_db()
    def test_clear_all_removes_tags_and_deletes_runs(self, client, team_with_users):
        user = team_with_users.members.first()
        config, chat_message, tag = self._setup_config_with_applied_tag(team_with_users)

        client.force_login(user)
        url = reverse("evaluations:clear_evaluation_runs", args=[team_with_users.slug, config.id])
        response = client.post(url)

        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == reverse(
            "evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id]
        )
        assert not chat_message.tags.filter(pk=tag.pk).exists()
        assert not EvaluationRun.objects.filter(config=config).exists()
        assert not EvaluationResult.objects.filter(run__config=config).exists()
        assert not AppliedTag.objects.filter(rule__evaluator__in=config.evaluators.all()).exists()

    @pytest.mark.django_db()
    def test_clear_all_preserves_manual_tags(self, client, team_with_users):
        user = team_with_users.members.first()
        config, chat_message, tag = self._setup_config_with_applied_tag(team_with_users, user=user)

        client.force_login(user)
        url = reverse("evaluations:clear_evaluation_runs", args=[team_with_users.slug, config.id])
        response = client.post(url)

        assert response.status_code == 200
        assert chat_message.tags.filter(pk=tag.pk).exists()
        assert not EvaluationRun.objects.filter(config=config).exists()

    @pytest.mark.django_db()
    def test_clear_all_leaves_other_config_untouched(self, client, team_with_users):
        user = team_with_users.members.first()
        config, _, _ = self._setup_config_with_applied_tag(team_with_users)
        other_config, other_chat_message, other_tag = self._setup_config_with_applied_tag(team_with_users)

        client.force_login(user)
        url = reverse("evaluations:clear_evaluation_runs", args=[team_with_users.slug, config.id])
        client.post(url)

        assert other_chat_message.tags.filter(pk=other_tag.pk).exists()
        assert EvaluationRun.objects.filter(config=other_config).exists()

    @pytest.mark.django_db()
    def test_clear_all_requires_delete_permission(self, client, team_with_users):
        view_perm = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(EvaluationRun),
            codename="view_evaluationrun",
        )
        limited_group = GroupFactory.create(name="evaluations-view-only")
        limited_group.permissions.add(view_perm)
        membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
        config, chat_message, tag = self._setup_config_with_applied_tag(team_with_users)

        client.force_login(membership.user)
        url = reverse("evaluations:clear_evaluation_runs", args=[team_with_users.slug, config.id])
        response = client.post(url)

        assert response.status_code == 403
        assert chat_message.tags.filter(pk=tag.pk).exists()
        assert EvaluationRun.objects.filter(config=config).exists()

    @pytest.mark.django_db()
    def test_runs_page_shows_clear_all_button_for_admin(self, client, team_with_users):
        user = team_with_users.members.first()
        config = EvaluationConfigFactory.create(team=team_with_users)

        client.force_login(user)
        url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id])
        response = client.get(url)

        clear_url = reverse("evaluations:clear_evaluation_runs", args=[team_with_users.slug, config.id])
        assert clear_url in response.content.decode()

    @pytest.mark.django_db()
    def test_runs_page_hides_clear_all_for_view_only(self, client, team_with_users):
        view_perm = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(EvaluationRun),
            codename="view_evaluationrun",
        )
        limited_group = GroupFactory.create(name="evaluations-view-only")
        limited_group.permissions.add(view_perm)
        membership = MembershipFactory.create(team=team_with_users, groups=[limited_group])
        config = EvaluationConfigFactory.create(team=team_with_users)

        client.force_login(membership.user)
        url = reverse("evaluations:evaluation_runs_home", args=[team_with_users.slug, config.id])
        response = client.get(url)

        clear_url = reverse("evaluations:clear_evaluation_runs", args=[team_with_users.slug, config.id])
        assert clear_url not in response.content.decode()
