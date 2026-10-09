import pytest

from ..engagement_forms import EngagementFilterForm


class TestEngagementFilterForm:
    def test_has_no_date_granularity_or_saved_filter_fields(self):
        form = EngagementFilterForm()

        forbidden = {"date_range", "start_date", "end_date", "granularity", "filter_data", "name", "is_default"}
        assert forbidden.isdisjoint(form.fields.keys())
        assert set(form.fields.keys()) == {"experiments", "channels", "participants", "tags"}

    @pytest.mark.django_db()
    def test_get_filter_params_scopes_querysets_to_team(self, team, experiment, participant):
        form = EngagementFilterForm(data={"experiments": [experiment.id]}, team=team)

        assert form.is_valid(), form.errors
        params = form.get_filter_params()

        assert params == {"experiment_ids": [experiment.id]}

    @pytest.mark.django_db()
    def test_get_filter_params_returns_empty_dict_when_nothing_selected(self, team):
        form = EngagementFilterForm(data={}, team=team)

        assert form.is_valid(), form.errors
        assert form.get_filter_params() == {}
