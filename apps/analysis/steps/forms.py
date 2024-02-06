import csv
from datetime import datetime

from django import forms
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.encoding import smart_bytes

from apps.analysis.core import Params, ParamsForm
from apps.analysis.models import Resource, ResourceType
from apps.service_providers.models import AuthProvider


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
                content_type=self.cleaned_data["file"].content_type,
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
    template_name = "analysis/forms/timeseries_filter.html"
    duration_value = forms.IntegerField(required=False, label="Duration")
    duration_unit = forms.TypedChoiceField(
        required=False, choices=get_duration_choices(), label="Duration Unit", coerce=int
    )
    anchor_mode = forms.ChoiceField(
        required=False,
        label="Starting from",
        choices=[
            ("relative_start", "Beginning of data"),
            ("relative_end", "End of data"),
            ("absolute", "Specific date"),
        ],
    )
    anchor_point = forms.DateField(required=False, label="Starting on", initial=timezone.now)
    minimum_data_points = forms.IntegerField(required=False, label="Minimum Data Points for Dataset", initial=10)
    calendar_time = forms.BooleanField(
        required=False,
        label="Use calendar periods",
        initial=True,
        help_text="If checked, the start and end times of the window will be adjusted to the nearest calendar period. "
        "For example, if the duration is 1 day, the window will start at midnight and end at 11:59:59 PM.",
    )

    def reformat_initial(self, initial):
        anchor_point = initial.get("anchor_point")
        if anchor_point and isinstance(anchor_point, str):
            initial["anchor_point"] = datetime.fromisoformat(anchor_point).date()
        return initial

    def clean_unit(self):
        from apps.analysis.steps.filters import DurationUnit

        try:
            return DurationUnit(self.cleaned_data["duration_unit"])
        except ValueError:
            raise forms.ValidationError("Invalid duration unit.")

    def get_params(self):
        from .filters import TimeseriesFilterParams

        data = dict(self.cleaned_data)
        if data["anchor_mode"] != "absolute":
            del data["anchor_point"]

        if data["minimum_data_points"] is None:
            del data["minimum_data_points"]

        try:
            return TimeseriesFilterParams(**data)
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


class StaticAssistantParamsForm(ParamsForm):
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


class DynamicAssistantParamsForm(ParamsForm):
    form_name = "Assistant Parameters"
    template_name = "analysis/forms/basic.html"
    prompt = forms.CharField(widget=forms.Textarea)

    def get_params(self):
        from .processors import AssistantParams

        try:
            return AssistantParams(prompt=self.cleaned_data["prompt"])
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


class CommCareAppLoaderStaticConfigForm(ParamsForm):
    form_name = "CommCare App Loader Configuration"
    template_name = "analysis/forms/basic.html"
    cc_url = forms.URLField(label="CommCare Base URL", required=True, initial="https://www.commcarehq.org")
    auth_provider = forms.ModelChoiceField(label="Authentication", queryset=None, required=False)
    app_list = forms.CharField(
        widget=forms.Textarea,
        label="Application List",
        help_text="Enter one app per line: domain, app_id, name",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["auth_provider"].queryset = _get_auth_provider_queryset(self.request)

    def reformat_initial(self, initial):
        if "app_list" in initial:
            initial["app_list"] = "\n".join(
                f"{app['domain']}, {app['app_id']}, {app['name']}" for app in initial["app_list"]
            )
        initial["auth_provider"] = initial.get("auth_provider_id", None)
        return initial

    def clean_cc_url(self):
        url = self.cleaned_data["cc_url"]
        if url.endswith("/"):
            url = url[:-1]
        return url

    def clean_app_list(self):
        app_list = self.cleaned_data["app_list"]
        csv_reader = csv.reader([line for line in app_list.splitlines() if line.strip()])
        return [{"domain": row[0].strip(), "app_id": row[1].strip(), "name": row[2].strip()} for row in csv_reader]

    def get_params(self):
        from .loaders import CommCareAppLoaderParams

        return CommCareAppLoaderParams(
            cc_url=self.cleaned_data["cc_url"],
            app_list=self.cleaned_data["app_list"],
            auth_provider_id=self.cleaned_data["auth_provider"].id,
        )


class CommCareAppLoaderParamsForm(ParamsForm):
    form_name = "CommCare App Loader Parameters"
    template_name = "analysis/forms/commcare_loader_params.html"
    select_app_id = forms.ChoiceField(label="Application", required=False)
    domain = forms.CharField(required=False, label="CommCare Project Space")
    app_id = forms.CharField(required=False, label="CommCare Application ID")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = kwargs.get("initial")
        if initial and initial.get("app_list"):
            self.fields["select_app_id"].choices = [(app["app_id"], app["name"]) for app in initial["app_list"]]

    def get_params(self):
        from .loaders import CommCareAppLoaderParams

        select_app_id = self.cleaned_data.get("select_app_id")
        app_id = self.cleaned_data.get("app_id")
        domain = self.cleaned_data.get("domain")
        if not select_app_id and not app_id and not domain:
            raise forms.ValidationError("Either an application or a domain and app_id must be provided.")

        if select_app_id:
            app_list = self.initial.get("app_list")
            app = next((app for app in app_list if app["app_id"] == select_app_id), None)
            app_id = app["app_id"]
            domain = app["domain"]

        try:
            return CommCareAppLoaderParams(
                cc_domain=domain,
                cc_app_id=app_id,
                cc_url=self.initial["cc_url"],
                auth_provider_id=self.initial["auth_provider_id"],
            )
        except ValueError as e:
            raise forms.ValidationError(repr(e))


def _get_auth_provider_queryset(request):
    return AuthProvider.objects.filter(team=request.team)
