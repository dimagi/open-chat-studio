import json

import pytest

from apps.annotations.models import Tag
from apps.help.agents.filter import FilterAgent, FilterInput
from apps.help.evals.conftest import FIXTURES_DIR, load_fixtures, run_checks
from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.pytest import django_db_with_data

cases = load_fixtures(FIXTURES_DIR / "filter.yml")


@pytest.mark.eval()
@pytest.mark.django_db()
@pytest.mark.parametrize("case", cases, ids=lambda c: c["id"])
def test_filter(case):
    team = TeamFactory()
    agent = FilterAgent(input=FilterInput(**case["input"], team_id=team.id))
    result = agent.run()
    run_checks(result, case["checks"], output_field="filters")


@pytest.mark.eval()
@django_db_with_data()
def test_filter_experiment_uses_option_ids():
    """Agent must call get_filter_options('experiment') and use the returned integer ID.

    ExperimentFilter.parse_query_value converts values to int(), so passing a name
    string like "Alpha Bot" would produce empty results. This test verifies the agent
    correctly resolves the experiment name to its numeric PK via the tool.
    """
    team = TeamFactory()
    experiment = ChatbotFactory(team=team, name="Alpha Bot")

    assert experiment.is_working_version
    agent = FilterAgent(
        input=FilterInput(
            query="alpha bot sessions",
            filter_slug="session",
            team_id=team.id,
        )
    )
    result = agent.run()

    filters_by_col = {f.column: f for f in result.filters}
    assert "experiment" in filters_by_col, (
        f"Expected 'experiment' filter in output, got columns: {sorted(filters_by_col)}"
    )
    exp_filter = filters_by_col["experiment"]
    assert exp_filter.operator == "any of", f"Expected 'any of', got {exp_filter.operator!r}"

    ids = json.loads(exp_filter.value)
    assert ids == [experiment.id], (
        f"Agent must use integer ID {experiment.id} from get_filter_options tool, got {ids!r}. "
        "Passing the name string would cause ExperimentFilter.parse_query_value to silently drop the value."
    )


@pytest.mark.eval()
@django_db_with_data()
def test_filter_tags_tool_lookup():
    """Agent should call get_filter_options('tags') to discover available tag names.

    Creates a tag for the team so the tool returns a non-empty options list,
    then verifies the agent uses the correct tag name in the filter value.
    """
    team = TeamFactory()
    Tag.objects.create(name="urgent", slug="urgent", team=team, is_system_tag=False, category="")
    Tag.objects.create(name="👎🏻", slug="👎🏻", team=team, is_system_tag=False, category="response_rating")

    agent = FilterAgent(
        input=FilterInput(
            query="sessions tagged with urgent or thumbs down",
            filter_slug="session",
            team_id=team.id,
        )
    )
    result = agent.run()

    filters_by_col = {f.column: f for f in result.filters}
    assert "tags" in filters_by_col, f"Expected 'tags' filter in output, got columns: {sorted(filters_by_col)}"
    tags_filter = filters_by_col["tags"]
    assert tags_filter.operator == "any of", f"Expected 'any of', got {tags_filter.operator!r}"

    tag_values = json.loads(tags_filter.value)
    assert "urgent" in tag_values, f"Expected 'urgent' in tag values, got {tag_values!r}"
    assert "👎🏻" in tag_values, f"Expected '👎🏻' in tag values, got {tag_values!r}"
