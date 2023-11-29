from django import forms

from apps.analysis.loaders import ResourceLoaderParams
from apps.analysis.models import Analysis, Resource, ResourceType
from apps.analysis.pipelines import get_source_pipeline_options
from apps.analysis.steps import Params


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


class ParamsForm(forms.Form):
    def __init__(self, request, *args, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def save(self) -> Params:
        raise NotImplementedError


class ResourceLoaderParamsForm(ParamsForm):
    template_name = "analysis/forms/resource_loader_params.html"
    resource = forms.ModelChoiceField(label="Existing File", queryset=None, required=False)
    file = forms.FileField(required=False, help_text="Alternatively upload a new file")
    file_type = forms.ChoiceField(required=False, choices=ResourceType.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resource"].queryset = Resource.objects.filter(team=self.request.team)

    def clean(self):
        cleaned_data = super().clean()
        file = cleaned_data.get("file")
        if file:
            file_type = cleaned_data.get("file_type")
            if not file_type:
                raise forms.ValidationError("File type must be provided when uploading a file.")
        elif not cleaned_data.get("resource"):
            raise forms.ValidationError("Either a resource or a file must be provided.")
        return cleaned_data

    def save(self) -> Params:
        if self.cleaned_data["file"]:
            resource = Resource.objects.create(
                team=self.request.team,
                name=self.cleaned_data["file"].name,
                type=self.cleaned_data["file_type"],
                file=self.cleaned_data["file"],
                content_size=self.cleaned_data["file"].size,
            )
        else:
            resource = self.cleaned_data["resource"]

        return ResourceLoaderParams(resource_id=resource.id)
