import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.evaluations.models import ConditionType, Evaluator
from apps.utils.factories.evaluations import EvaluationTagFactory, EvaluatorTagRuleFactory
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import GroupFactory

INSUFFICIENT_COPY_PERMISSIONS = [
    pytest.param(("view_evaluator",), id="view-only"),
    pytest.param(("view_evaluator", "add_evaluator"), id="add-without-change"),
]


@pytest.fixture()
def evaluator_member(team_with_users):
    """Build a team member whose only evaluator permissions are the given codenames."""

    def _make(*codenames):
        """Create a membership in a fresh group holding only these evaluator permissions."""
        content_type = ContentType.objects.get_for_model(Evaluator)
        group = GroupFactory.create(name="evaluator-" + "-".join(codenames))
        group.permissions.add(*Permission.objects.filter(content_type=content_type, codename__in=codenames))
        return MembershipFactory.create(team=team_with_users, groups=[group])

    return _make


@pytest.fixture()
def seed_llm_pricing():
    """Add global input and output token prices for one model."""

    def _seed(provider_type: str, model_name: str):
        for kind, unit_price in ((ServiceKind.LLM_INPUT, "0.00015"), (ServiceKind.LLM_OUTPUT, "0.00060")):
            PricingRule.objects.create(
                team=None, provider_type=provider_type, model_name=model_name, service_kind=kind, unit_price=unit_price
            )

    return _seed


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
