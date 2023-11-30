from django import forms

from apps.analysis.core import Params, ParamsForm
from apps.analysis.models import Analysis, Resource, ResourceType
from apps.analysis.pipelines import get_source_pipeline_options


class AnalysisForm(forms.ModelForm):
    class Meta:
        model = Analysis
        fields = [
            "name",
            "source",
            "pipelines",
            "llm_provider",
        ]
        widgets = {
            "source": forms.Select(choices=get_source_pipeline_options()),
        }
