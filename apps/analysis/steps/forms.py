from django import forms
from django.core.files.base import ContentFile

from apps.analysis.core import Params, ParamsForm
from apps.analysis.models import Resource, ResourceType


class ResourceLoaderParamsForm(ParamsForm):
    form_name = "Resource Loader Parameters"
    template_name = "analysis/forms/resource_loader_params.html"
    resource = forms.ModelChoiceField(label="Existing File", queryset=None, required=False)
    file = forms.FileField(required=False, help_text="Alternatively upload a new file")
    file_type = forms.ChoiceField(required=False, choices=ResourceType.choices)
    text = forms.CharField(required=False, widget=forms.Textarea)

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
        elif not cleaned_data.get("text") and not cleaned_data.get("resource"):
            raise forms.ValidationError("Either a resource or a file or text must be provided.")
        return cleaned_data

    def save(self) -> Params:
        from apps.analysis.steps.loaders import ResourceLoaderParams

        if self.cleaned_data["file"]:
            resource = Resource.objects.create(
                team=self.request.team,
                name=self.cleaned_data["file"].name,
                type=self.cleaned_data["file_type"],
                file=self.cleaned_data["file"],
                content_size=self.cleaned_data["file"].size,
            )
        elif self.cleaned_data["text"]:
            resource = Resource.objects.create(
                team=self.request.team,
                name=f"Text ({self.cleaned_data['text'][:20]}...)",
                type=ResourceType.TEXT,
            )
            resource.file.save(f"{resource.name}.txt", ContentFile(self.cleaned_data["text"]))
        else:
            resource = self.cleaned_data["resource"]

        return ResourceLoaderParams(resource_id=resource.id)


class LlmCompletionStepParamsForm(ParamsForm):
    form_name = "LLM Completion Parameters"
    prompt = forms.CharField(widget=forms.Textarea)

    def clean(self):
        self._get_params(super().clean())

    def save(self):
        return self._get_params(self.cleaned_data)

    def _get_params(self, cleaned_data):
        from .processors import LlmCompletionStepParams

        try:
            return LlmCompletionStepParams(prompt=self.cleaned_data["prompt"])
        except ValueError as e:
            raise forms.ValidationError(repr(e))


def get_duration_choices():
    from apps.analysis.steps.filters import DurationUnit

    return [(unit.name, unit.name.title) for unit in list(DurationUnit)]


class TimeseriesFilterForm(ParamsForm):
    form_name = "Timeseries Filter Parameters"
    template_name = "analysis/forms/timeseries_filter_params.html"
    value = forms.IntegerField(required=False, label="Duration")
    unit = forms.ChoiceField(required=False, choices=get_duration_choices(), label="Duration Unit")
    anchor = forms.DateField(required=False, label="Starting on")

    def clean_unit(self):
        from apps.analysis.steps.filters import DurationUnit

        try:
            return DurationUnit[self.cleaned_data["unit"]]
        except KeyError:
            raise forms.ValidationError("Invalid duration unit.")

    def clean(self):
        self._get_params(super().clean())

    def save(self):
        return self._get_params(self.cleaned_data)

    def _get_params(self, cleaned_data):
        from .filters import DateRange, Duration, TimeseriesFilterParams

        duration = Duration(unit=cleaned_data["unit"], value=cleaned_data["value"])
        date_range = DateRange(duration=duration, anchor_type="this", anchor_point=cleaned_data["anchor"])
        try:
            return TimeseriesFilterParams(date_range=date_range)
        except ValueError as e:
            raise forms.ValidationError(repr(e))
