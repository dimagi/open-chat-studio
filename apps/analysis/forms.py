from django import forms

from apps.analysis.models import Analysis
from apps.analysis.pipelines import get_data_pipeline_options, get_source_pipeline_options


class AnalysisForm(forms.ModelForm):
    class Meta:
        model = Analysis
        fields = [
            "name",
            "source",
            "pipeline",
            "llm_provider",
            "llm_model",
        ]
        widgets = {
            "source": forms.Select(choices=get_source_pipeline_options()),
            "pipeline": forms.Select(choices=get_data_pipeline_options()),
        }

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        self.fields["llm_provider"].queryset = self.request.team.llmprovider_set
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProvider",
        }
        self.fields["llm_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"
