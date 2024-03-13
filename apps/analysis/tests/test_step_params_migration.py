import pytest

from apps.analysis.migration_utils import migrate_step_params
from apps.analysis.models import Analysis
from apps.analysis.pipelines import COMMCARE_APP_PIPE, LLM_PIPE, get_data_pipeline, get_source_pipeline
from apps.analysis.tests.factories import AnalysisFactory, RunGroupFactory


@pytest.mark.django_db()
def test_migrate_step_params():
    config = {}
    steps = get_source_pipeline(COMMCARE_APP_PIPE).steps + get_data_pipeline(LLM_PIPE).steps
    for step in steps:
        config[step.__class__.__name__] = {f"{step.__class__.__name__}_param": 1}
    analysis = AnalysisFactory(source=COMMCARE_APP_PIPE, pipeline=LLM_PIPE, config=config)
    runs = [RunGroupFactory(team=analysis.team, analysis=analysis, params=config) for i in range(2)]
    migrate_step_params(Analysis)
    analysis.refresh_from_db()
    expected_params = {
        "CommCareAppLoader:source:0": {"CommCareAppLoader_param": 1},
        "JinjaTemplateStep:source:1": {"JinjaTemplateStep_param": 1},
        "LlmCompletionStep:data:0": {"LlmCompletionStep_param": 1},
    }
    assert analysis.config == expected_params
    for run in runs:
        run.refresh_from_db()
        assert run.params == expected_params
