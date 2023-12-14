from apps.analysis.utils import merge_raw_params


def test_merge_raw_params_with_empty_dicts():
    result = merge_raw_params({}, {}, {})
    assert result == {}


def test_merge_raw_params_with_single_dict():
    result = merge_raw_params({"step1": {"param1": "value1"}})
    assert result == {"step1": {"param1": "value1"}}


def test_merge_raw_params_with_multiple_dicts_same_keys():
    result = merge_raw_params(
        {"step1": {"param1": "value1"}},
        {"step1": {"param2": "value2"}},
        {"step1": {"param3": "value3"}},
    )
    assert result == {"step1": {"param1": "value1", "param2": "value2", "param3": "value3"}}


def test_merge_raw_params_with_multiple_dicts_different_keys():
    result = merge_raw_params(
        {"step1": {"param1": "value1"}},
        {"step2": {"param2": "value2"}},
    )
    assert result == {
        "step1": {"param1": "value1"},
        "step2": {"param2": "value2"},
    }


def test_merge_raw_params_with_overlapping_keys():
    result = merge_raw_params(
        {"step1": {"param1": "value1"}},
        {"step1": {"param1": "new_value1", "param2": "value2"}},
    )
    assert result == {"step1": {"param1": "new_value1", "param2": "value2"}}


def test_merge_raw_params_with_non_dict_values():
    result = merge_raw_params(
        {"step1": "value1"},
        {"step2": "value2"},
        {"step3": "value3"},
    )
    assert result == {
        "step1": "value1",
        "step2": "value2",
        "step3": "value3",
    }
