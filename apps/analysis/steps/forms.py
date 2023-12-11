from django import forms
from django.core.files.base import ContentFile
from django.utils.encoding import smart_bytes

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

    def reformat_initial(self, initial):
        resource_id = initial.get("resource_id")
        if resource_id:
            return {"resource": Resource.objects.get(id=resource_id)}

    def clean(self):
        cleaned_data = self.cleaned_data
        file = cleaned_data.get("file")
        if file:
            file_type = cleaned_data.get("file_type")
            if not file_type:
                raise forms.ValidationError("File type must be provided when uploading a file.")
        elif not cleaned_data.get("text") and not cleaned_data.get("resource"):
            raise forms.ValidationError("Either a resource or a file or text must be provided.")

        return super().clean()

    def get_params(self) -> Params:
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

            data_bytes = smart_bytes(self.cleaned_data["text"])
            resource.file.save(f"{resource.name}.txt", ContentFile(data_bytes))
        else:
            resource = self.cleaned_data["resource"]

        return ResourceLoaderParams(resource_id=resource.id)


class LlmCompletionStepParamsForm(ParamsForm):
    form_name = "LLM Completion Parameters"
    prompt = forms.CharField(widget=forms.Textarea)

    def get_params(self):
        from .processors import LlmCompletionStepParams

        try:
            return LlmCompletionStepParams(prompt=self.cleaned_data["prompt"])
        except ValueError as e:
            raise forms.ValidationError(repr(e))


def get_duration_choices():
    from apps.analysis.steps.filters import DurationUnit

    return [(unit.value, unit.name.title) for unit in list(DurationUnit)]


class TimeseriesFilterForm(ParamsForm):
    form_name = "Timeseries Filter Parameters"
    template_name = "analysis/forms/basic.html"
    duration_value = forms.IntegerField(required=False, label="Duration")
    duration_unit = forms.TypedChoiceField(
        required=False, choices=get_duration_choices(), label="Duration Unit", coerce=int
    )
    anchor_point = forms.DateField(required=False, label="Starting on")

    def clean_unit(self):
        from apps.analysis.steps.filters import DurationUnit

        try:
            return DurationUnit(self.cleaned_data["duration_unit"])
        except ValueError:
            raise forms.ValidationError("Invalid duration unit.")

    def get_params(self):
        from .filters import TimeseriesFilterParams

        try:
            return TimeseriesFilterParams(**self.cleaned_data)
        except ValueError as e:
            raise forms.ValidationError(repr(e))


def get_time_groups():
    from apps.analysis.steps.splitters import TimeGroup

    return [(group.value, group.name.title) for group in list(TimeGroup)]


class TimeseriesSplitterParamsForm(ParamsForm):
    form_name = "Timeseries Splitter Parameters"
    template_name = "analysis/forms/basic.html"
    time_group = forms.ChoiceField(required=False, choices=get_time_groups(), label="Group By")
    origin = forms.ChoiceField(
        required=False,
        choices=[("start", "Beginning of data"), ("end", "End of data")],
        label="Start from",
        help_text="Align the groups with the beginning or end of the data.",
        initial="start",
    )
    ignore_empty_groups = forms.BooleanField(required=False, label="Ignore Empty Groups", initial=True)

    def clean_time_group(self):
        from apps.analysis.steps.splitters import TimeGroup

        try:
            return TimeGroup(self.cleaned_data["time_group"])
        except KeyError:
            raise forms.ValidationError("Invalid time group.")

    def get_params(self):
        from .splitters import TimeseriesSplitterParams

        try:
            return TimeseriesSplitterParams(
                time_group=self.cleaned_data["time_group"],
                origin=self.cleaned_data["origin"],
                ignore_empty_groups=self.cleaned_data["ignore_empty_groups"],
            )
        except ValueError as e:
            raise forms.ValidationError(repr(e))


class AssistantParamsForm(ParamsForm):
    form_name = "Assistant Parameters"
    template_name = "analysis/forms/basic.html"
    assistant_id = forms.CharField()
    prompt = forms.CharField(widget=forms.Textarea)

    def get_params(self):
        from .processors import AssistantParams

        try:
            return AssistantParams(assistant_id=self.cleaned_data["assistant_id"], prompt=self.cleaned_data["prompt"])
        except ValueError as e:
            raise forms.ValidationError(repr(e))


class WhatsappParserParamsForm(ParamsForm):
    form_name = "WhatsApp Parser Parameters"
    template_name = "analysis/forms/basic.html"
    remove_deleted_messages = forms.BooleanField(required=False, label="Remove Deleted Messages", initial=True)
    remove_system_messages = forms.BooleanField(required=False, label="Remove System Messages", initial=True)
    remove_media_omitted_messages = forms.BooleanField(
        required=False, label="Remove 'Media Omitted' Messages", initial=True
    )

    def get_params(self):
        from .parsers import WhatsappParserParams

        try:
            return WhatsappParserParams(
                remove_deleted_messages=self.cleaned_data["remove_deleted_messages"],
                remove_system_messages=self.cleaned_data["remove_system_messages"],
                remove_media_omitted_messages=self.cleaned_data["remove_media_omitted_messages"],
            )
        except ValueError as e:
            raise forms.ValidationError(repr(e))
