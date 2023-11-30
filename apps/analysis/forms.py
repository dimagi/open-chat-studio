from django import forms

from apps.analysis.models import Analysis
from apps.analysis.pipelines import get_data_pipeline_options, get_source_pipeline_options


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
            "pipelines": forms.Select(choices=get_data_pipeline_options()),
        }
