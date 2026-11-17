from types import SimpleNamespace

import pytest

from apps.pipelines.migrations.utils.strip_node_resource_params import (
    _parse_node,
    _validate_node,
)


def _node(params):
    """Build the small node shape the migration parser needs without a database."""
    return SimpleNamespace(id=7, pipeline_id=11, flow_id="llm-7", params=params)


def test_parse_node_normalizes_all_supported_resource_families():
    """Legacy string ids become integers while ordinary keys remain outside the parsed resources."""
    parsed, anomalies = _parse_node(
        _node(
            {
                "name": "assistant",
                "llm_provider_id": "3",
                "llm_provider_model_id": 4,
                "collection_index_ids": ["8", 9],
                "custom_actions": ["12:weather_get"],
            }
        )
    )

    assert anomalies == []
    assert parsed.scalar == {"llm_provider_id": 3, "llm_provider_model_id": 4}
    assert parsed.collection_indexes == {8, 9}
    assert parsed.custom_actions == {(12, "weather_get")}
    assert parsed.resource_keys == {
        "llm_provider_id",
        "llm_provider_model_id",
        "collection_index_ids",
        "custom_actions",
    }


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param({"llm_provider_id": 1.5}, "llm_provider_id is not an integer", id="float-scalar"),
        pytest.param({"collection_index_ids": "8"}, "collection_index_ids is not a list", id="scalar-list"),
        pytest.param({"custom_actions": ["not-a-reference"]}, "malformed custom action", id="bad-action"),
    ],
)
def test_parse_node_reports_malformed_resource_data(params, message):
    """Malformed historical values abort the migration instead of being silently discarded."""
    _parsed, anomalies = _parse_node(_node(params))

    assert len(anomalies) == 1
    assert message in anomalies[0]


def test_validate_node_rejects_wrong_team_and_wrong_collection_type():
    """A resource with a valid id is still unsafe when ownership or resource family is wrong."""
    node = _node({"collection_id": 5, "collection_index_ids": [6]})
    node.pipeline = SimpleNamespace(team_id=10)
    node.collection_id = None

    parsed, parse_anomalies = _parse_node(node)
    assert parse_anomalies == []
    anomalies = _validate_node(
        node,
        parsed,
        {
            "collection_id": {5: {"id": 5, "team_id": 99, "is_index": False}},
            "collections_indexes": {6: {"id": 6, "team_id": 10, "is_index": False}},
            "current_indexes": {},
            "custom_actions": {},
            "current_actions": {},
            "team_provider_types": {},
        },
    )

    assert any("another team's resource" in anomaly for anomaly in anomalies)
    assert any("invalid collection" in anomaly for anomaly in anomalies)
