"""The reference check's resolvers: which of a team's values a write will actually take (#4140).

`/pipeline/options/` publishes what a client may choose from, and a write refuses anything else
(`apps/api/v2/pipeline_edit/references.py`). The two have to agree, so the promise is stated here as
an equality: for every checked param, the resolver's answer is exactly the option list's contents.

The resolvers themselves live in `apps/pipelines/nodes/node_metadata.py` and are reached through
`OptionsSource.get_resolver`; what the discovery endpoints serve is in `test_pipeline_discovery.py`.
"""

import pytest
from django.urls import reverse

from apps.api.v2.discovery.contract import PARAMETER_OPTION_SOURCES
from apps.api.v2.discovery.node_types import _sources_by_type, parameter_option_mapping
from apps.api.v2.discovery.options import options_for_team
from apps.pipelines.models import Node
from apps.pipelines.nodes.base import OptionsSource
from apps.pipelines.nodes.node_metadata import RESOLVERS
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

from .test_pipeline_discovery import add_remaining_resources, make_team_with_resources


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def team_with_every_resource(db):
    """One entry in every option list, so a shape assertion has something to look at in each."""
    return add_remaining_resources(make_team_with_resources())


def _checked_params(client) -> dict[str, OptionsSource]:
    """`param -> the option key it draws from`, across every served type."""
    return {
        param: option_key
        for entry in client.get(reverse("api:v2:pipeline-nodes")).json()
        for param, option_key in parameter_option_mapping(entry["type"]).items()
    }


@pytest.mark.django_db()
def test_every_checked_param_has_a_resolver(team):
    """A write checks each reference param through the resolver on the source it draws from. A source
    with no resolver raises rather than writing, so this is the loud version of that."""
    for param, source in _checked_params(ApiTestClient(team.members.first(), team)).items():
        assert source.get_resolver(), f"{param} -> {source}"


def test_every_checked_source_has_a_resolver():
    """The test above only reaches the sources some served type declares, leaving the ones behind a
    deprecated type -- ``assistant`` today -- resting on nothing. Serving such a type again should
    not be what discovers its resolver is missing, so the whole checked set is held to having one.
    """
    assert set(RESOLVERS) >= PARAMETER_OPTION_SOURCES


def test_a_source_that_can_deny_nothing_has_no_resolver():
    """The other half of the promise above: asking for a resolver the registry does not hold raises
    rather than answering permissively, so a param wired to such a source cannot go unchecked."""
    unchecked = sorted(set(OptionsSource) - set(RESOLVERS), key=str)
    assert unchecked, "every source has a resolver, so this would prove nothing"
    for source in unchecked:
        with pytest.raises(NotImplementedError, match="has no resolver"):
            source.get_resolver()


@pytest.mark.django_db()
def test_a_write_accepts_exactly_the_values_the_options_endpoint_offers(team_with_every_resource):
    """The façade's one hard promise: an id `/pipeline/options/` offered is an id a write takes, and
    nothing else is.

    Checked against a second team holding the same kinds of resource, so each resolver has real ids
    to refuse as well as accept. `ours` is the whole expected answer even for the lists the two teams
    share, since the values those hold -- tool names, global LLM models -- are in `ours` already.
    """
    team = team_with_every_resource
    other = add_remaining_resources(make_team_with_resources())
    client = ApiTestClient(team.members.first(), team)

    for param, option_key in _checked_params(client).items():
        ours = {option["value"] for option in options_for_team(team)[option_key]}
        theirs = {option["value"] for option in options_for_team(other)[option_key]}
        assert ours, f"{option_key} offers nothing, so this would prove nothing"
        assert option_key.get_resolver()(team, sorted(ours | theirs)) == ours, param


@pytest.mark.django_db()
def test_a_collection_and_an_index_are_not_interchangeable(team_with_every_resource):
    """The one pair a resolver could confuse without either option list noticing: both are
    ``Collection`` rows in one id space, split only by ``is_index``. Every other reference param
    draws on a table of its own, so the test above already shows it ids it must refuse.
    """
    team = team_with_every_resource
    options = options_for_team(team)
    collections = {option["value"] for option in options["collection"]}
    indexes = {option["value"] for option in options["collection_index"]}
    assert collections, "no collections offered, so this would prove nothing"
    assert indexes, "no indexes offered, so this would prove nothing"

    both = sorted(collections | indexes)
    assert OptionsSource.collection.get_resolver()(team, both) == collections
    assert OptionsSource.collection_index.get_resolver()(team, both) == indexes


@pytest.mark.django_db()
def test_an_unknown_tool_name_is_refused(team):
    """The one resolver the equality test above cannot exercise: the tool names are a fixed
    vocabulary, so both teams are offered the same set and there is nothing of the other team's to
    refuse. What it must refuse is a name no team holds, and a value that is not a name at all --
    the vocabulary is a set, so asking whether an unhashable value is in it would raise.
    """
    offered = {option["value"] for option in options_for_team(team)["tools"]}
    assert offered, "no tools offered, so this would prove nothing"

    assert OptionsSource.tools.get_resolver()(team, sorted(offered)) == offered
    assert OptionsSource.tools.get_resolver()(team, ["not_a_tool"]) == set()
    assert OptionsSource.tools.get_resolver()(team, [{"a": 1}, ["nested"], 7, None]) == set()


@pytest.mark.django_db()
def test_unchecked_params_offer_no_team_values(team_with_every_resource):
    """The inverse, and the reason `PARAMETER_OPTION_SOURCES` can be stated as an allowlist.

    A source left out of it is never checked against anything, so its option list must hold nothing
    a team could be denied. The lists that qualify today are the prompt variables and the two tool
    lists. A new source carrying team-scoped `value` entries fails here until it is named a
    reference.
    """
    client = ApiTestClient(team_with_every_resource.members.first(), team_with_every_resource)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    for node_type, sources in _sources_by_type().items():
        for param, source in sources.items():
            if source in PARAMETER_OPTION_SOURCES:
                continue
            offered = options[source]
            where = f"{node_type}.{param} -> {source} is checked against nothing"
            assert not isinstance(offered, list) or not any("value" in option for option in offered), where


@pytest.mark.django_db()
def test_reference_sources_match_the_ones_the_schemas_use(team):
    """`PARAMETER_OPTION_SOURCES` is matched against the `ui:optionsSource` values read off the node
    schemas, and the two sides are maintained apart -- one in `contract.py`, one on the pydantic
    fields. A rename on either would quietly empty the intersection, leaving every write unchecked."""
    client = ApiTestClient(team.members.first(), team)

    checked = {
        param
        for entry in client.get(reverse("api:v2:pipeline-nodes")).json()
        for param in parameter_option_mapping(entry["type"])
    }

    assert {"llm_provider_id", "collection_index_ids", "custom_actions"} <= checked


def test_every_mirrored_resource_param_is_checked():
    """A param mirrored to one of `Node`'s resource FK columns names a row of the team's, so it has to
    draw from a source `PARAMETER_OPTION_SOURCES` names -- otherwise the id lands in the FK column
    unchecked and `Pipeline.validate` says nothing about it.

    That set is derived from the param names by stripping an `_id`/`_ids` suffix, which holds only as
    long as an option list stays named after the param that reads it. Here that assumption meets what
    the schemas declare: a mirrored param whose `ui:optionsSource` is not the one its name implies
    falls out of the derived set and fails here rather than going unchecked on every write.
    """
    mirrored = Node.resource_param_names()

    unchecked = {
        f"{node_type}.{param} -> {source}"
        for node_type, sources in _sources_by_type().items()
        for param, source in sources.items()
        if param in mirrored and source not in PARAMETER_OPTION_SOURCES
    }

    assert not unchecked, f"mirrored resource params drawing from an unchecked source: {sorted(unchecked)}"
