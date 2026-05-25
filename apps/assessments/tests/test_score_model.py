from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError

from apps.assessments.models import Score
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_score_can_be_created_with_numeric_value():
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    score = Score.objects.create(
        team=team,
        target_content_type=ContentType.objects.get_for_model(session),
        target_object_id=session.id,
        name="quality",
        data_type=Score.DataType.NUMERIC,
        value_numeric=Decimal("0.75"),
        source=Score.Source.LLM_JUDGE,
    )
    assert score.target == session
    assert score.value_numeric == Decimal("0.75")
    assert score.value_string is None


@pytest.mark.django_db()
def test_score_can_be_created_with_string_value():
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    score = Score.objects.create(
        team=team,
        target_content_type=ContentType.objects.get_for_model(session),
        target_object_id=session.id,
        name="sentiment",
        data_type=Score.DataType.CATEGORICAL,
        value_string="positive",
        source=Score.Source.HUMAN_REVIEW,
    )
    assert score.value_string == "positive"
    assert score.value_numeric is None


@pytest.mark.django_db()
def test_score_requires_a_value_present():
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    with pytest.raises(IntegrityError, match="score_value_present"):
        Score.objects.create(
            team=team,
            target_content_type=ContentType.objects.get_for_model(session),
            target_object_id=session.id,
            name="empty",
            data_type=Score.DataType.NUMERIC,
            source=Score.Source.SYSTEM,
        )
