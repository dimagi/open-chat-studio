import importlib
import json

from django import forms
from django.db.models import Q
from pydantic import ValidationError as PydanticValidationError

from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
    Evaluator,
    ExperimentVersionSelection,
)
from apps.experiments.models import Experiment, ExperimentSession


class ExperimentChoiceField(forms.ChoiceField):
    def __init__(self, queryset, *args, **kwargs):
        self.queryset = queryset
        # Add sentinel values first
        choices = [
            (ExperimentVersionSelection.LATEST_WORKING.value, ExperimentVersionSelection.LATEST_WORKING.label),
            (ExperimentVersionSelection.LATEST_PUBLISHED.value, ExperimentVersionSelection.LATEST_PUBLISHED.label),
        ]
        if queryset is not None:
            choices.extend((str(obj.pk), str(obj)) for obj in queryset)
        kwargs["choices"] = choices
        super().__init__(*args, **kwargs)


class EvaluationConfigForm(forms.ModelForm):
    # Add a helper field for selecting the base chatbot first
    experiment = forms.ModelChoiceField(
        queryset=Experiment.objects.none(),
        required=False,
        empty_label="Select a chatbot for generation...",
        help_text="Select the chatbot to run generation against",
        widget=forms.Select(attrs={"class": "select w-full"}),
        label="Chatbot",
    )
    experiment_version = None  # Created dynamically based on the queryset

    class Meta:
        model = EvaluationConfig
        fields = [
            "name",
            "evaluators",
            "dataset",
            "experiment_version",
            "base_experiment",
        ]
        widgets = {
            "evaluators": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

        self.fields["dataset"].queryset = EvaluationDataset.objects.filter(team=team)
        self.fields["evaluators"].queryset = Evaluator.objects.filter(team=team)
        self.fields["experiment_version"].queryset = Experiment.objects.filter(team=team)

        self.fields["experiment"].queryset = Experiment.objects.filter(
            team=team, working_version__isnull=True
        ).order_by("name")

        experiment_version_queryset = None

        if self.instance and self.instance.pk:
            if self.instance.experiment_version:
                # For specific version, set experiment field based on the experiment_version
                experiment_version = self.instance.experiment_version
                working_version_id = experiment_version.working_version_id or experiment_version.id
                working_experiment = Experiment.objects.filter(team=self.team, id=working_version_id).first()

                if working_experiment:
                    self.initial["experiment"] = working_experiment
                    # Filter the experiment_version queryset to only show versions for this experiment
                    experiment_version_queryset = self._get_version_choices(working_experiment)

            elif self.instance.base_experiment:
                # For sentinel values, set experiment and convert sentinel type to string
                self.initial["experiment"] = self.instance.base_experiment
                # Filter the experiment_version queryset to only show versions for this experiment
                experiment_version_queryset = self._get_version_choices(self.instance.base_experiment)
                if self.instance.version_selection_type == ExperimentVersionSelection.LATEST_WORKING:
                    self.initial["experiment_version"] = ExperimentVersionSelection.LATEST_WORKING.value
                elif self.instance.version_selection_type == ExperimentVersionSelection.LATEST_PUBLISHED:
                    self.initial["experiment_version"] = ExperimentVersionSelection.LATEST_PUBLISHED.value

        self.fields["experiment_version"] = ExperimentChoiceField(
            queryset=experiment_version_queryset,
            required=False,
            widget=forms.Select(attrs={"class": "select w-full"}),
            label="Chatbot version",
            help_text="Choose a chatbot version",
        )

    def _get_version_choices(self, experiment):
        """Get all versions for a specific experiment including working version"""

        working_version_id = experiment.working_version_id or experiment.id
        return (
            Experiment.objects.filter(team=self.team)
            .filter(Q(working_version_id=working_version_id) | Q(id=working_version_id))
            .order_by("-version_number")
        )

    def clean(self):
        cleaned_data = super().clean()

        experiment_version = cleaned_data.get("experiment_version")
        experiment = cleaned_data.get("experiment")

        if experiment_version in {
            ExperimentVersionSelection.LATEST_WORKING,
            ExperimentVersionSelection.LATEST_PUBLISHED,
        }:
            if experiment_version == ExperimentVersionSelection.LATEST_WORKING:
                cleaned_data["version_selection_type"] = ExperimentVersionSelection.LATEST_WORKING
                cleaned_data["experiment_version"] = None
            elif experiment_version == ExperimentVersionSelection.LATEST_PUBLISHED:
                cleaned_data["version_selection_type"] = ExperimentVersionSelection.LATEST_PUBLISHED
                cleaned_data["experiment_version"] = None
        else:
            cleaned_data["version_selection_type"] = ExperimentVersionSelection.SPECIFIC
            try:
                if experiment.id == int(experiment_version):
                    cleaned_data["experiment_version"] = Experiment.objects.get(team=self.team, id=experiment_version)
                else:
                    cleaned_data["experiment_version"] = Experiment.objects.get(
                        team=self.team, working_version_id=experiment.id, id=experiment_version
                    )
            except Experiment.DoesNotExist:
                self.add_error("experiment_version", "This experiment version was not found")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        cleaned_data = self.cleaned_data
        instance.version_selection_type = cleaned_data["version_selection_type"]
        instance.experiment_version = cleaned_data["experiment_version"]

        # Set base_experiment for sentinel values
        experiment = cleaned_data.get("experiment")
        if experiment and cleaned_data["version_selection_type"] in [
            ExperimentVersionSelection.LATEST_WORKING,
            ExperimentVersionSelection.LATEST_PUBLISHED,
        ]:
            instance.base_experiment = experiment
        else:
            instance.base_experiment = None

        if commit:
            instance.save()
            self.save_m2m()
        return instance


class EvaluatorForm(forms.ModelForm):
    class Meta:
        model = Evaluator
        fields = ("name", "type", "params")
        widgets = {
            "type": forms.HiddenInput(),
            "params": forms.HiddenInput(),
        }

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean(self):
        cleaned_data = super().clean()

        params = self.cleaned_data.get("params")
        evaluator_type = self.cleaned_data.get("type")

        if not evaluator_type:
            raise forms.ValidationError("Missing evaluator type")

        if isinstance(params, str):
            try:
                params = json.loads(params) or {}
            except json.JSONDecodeError as err:
                raise forms.ValidationError("Invalid JSON format for parameters") from err

        try:
            evaluators_module = importlib.import_module("apps.evaluations.evaluators")
            evaluator_class = getattr(evaluators_module, evaluator_type)

            evaluator_class(**params)

        except AttributeError as err:
            raise forms.ValidationError(f"Unknown evaluator type: {evaluator_type}") from err
        except PydanticValidationError as err:
            error_messages = []
            for error in err.errors():
                field_name = error["loc"][0] if error["loc"] else "unknown"
                message = error["msg"]
                error_messages.append(f"{field_name.replace('_', ' ').title()}: {message}")
            raise forms.ValidationError(f"{', '.join(error_messages)}") from err

        return cleaned_data


class EvaluationMessageForm(forms.ModelForm):
    class Meta:
        model = EvaluationMessage
        fields = ("input", "output", "context")


class EvaluationDatasetForm(forms.ModelForm):
    """Form for creating evaluation datasets."""

    MODE_CHOICES = [
        ("clone", "Clone from sessions"),
        ("manual", "Create manually"),
    ]

    mode = forms.ChoiceField(
        choices=MODE_CHOICES, widget=forms.RadioSelect, initial="clone", label="Dataset creation mode"
    )

    session_ids = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    messages_json = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    class Meta:
        model = EvaluationDataset
        fields = ("name",)

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean_name(self):
        name = self.cleaned_data.get("name")
        if name and EvaluationDataset.objects.filter(name=name, team=self.team).exists():
            raise forms.ValidationError("A dataset with this name already exists in your team.")

        return name

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("mode")

        if mode == "clone":
            session_ids_str = self.data.get("session_ids", "")
            session_ids = set(session_ids_str.split(","))
            session_ids.discard("")  # Remove empty strings

            if not session_ids:
                raise forms.ValidationError("At least one session must be selected when cloning from sessions.")

            existing_sessions = ExperimentSession.objects.filter(
                team=self.team, external_id__in=session_ids
            ).values_list("external_id", flat=True)

            missing_sessions = set(session_ids) - set(existing_sessions)
            if missing_sessions:
                raise forms.ValidationError(
                    "The following sessions do not exist or you don't have permission to access them: "
                    f"{', '.join(missing_sessions)}"
                )

            cleaned_data["session_ids"] = session_ids

        elif mode == "manual":
            messages_json = self.data.get("messages_json", "")
            cleaned_data["message_pairs"] = []
            if not messages_json:
                raise forms.ValidationError("At least one message pair must be added when creating manually.")

            try:
                message_pairs = json.loads(messages_json)
            except json.JSONDecodeError as err:
                raise forms.ValidationError("Messages data is invalid JSON.") from err

            if not isinstance(message_pairs, list) or len(message_pairs) == 0:
                raise forms.ValidationError("At least one message pair must be added.")

            for i, pair in enumerate(message_pairs):
                if not isinstance(pair, dict):
                    raise forms.ValidationError(f"Message pair {i + 1} is not a valid object.")
                if not pair.get("human", "").strip():
                    raise forms.ValidationError(f"Message pair {i + 1} is missing human message content.")
                if not pair.get("ai", "").strip():
                    raise forms.ValidationError(f"Message pair {i + 1} is missing AI message content.")
                if pair.get("context"):
                    try:
                        json.loads(pair.get("context", "{}"))
                    except json.JSONDecodeError as err:
                        raise forms.ValidationError(f"Context for pair {i + 1} has malformed JSON") from err

                cleaned_data["message_pairs"].append(
                    {
                        "human": EvaluationMessageContent(
                            content=pair.get("human", "").strip(), role="human"
                        ).model_dump(),
                        "ai": EvaluationMessageContent(content=pair.get("ai", "").strip(), role="ai").model_dump(),
                        "context": pair.get("context"),
                    }
                )

        return cleaned_data

    def save(self, commit=True):
        """Create dataset based on the selected mode."""
        dataset = super().save(commit=False)

        if not commit:
            return dataset

        dataset.save()

        mode = self.cleaned_data.get("mode")
        evaluation_messages = []

        if mode == "clone":
            session_ids = self.cleaned_data.get("session_ids", [])
            if session_ids:
                evaluation_messages = EvaluationMessage.create_from_sessions(self.team, session_ids)
        elif mode == "manual":
            evaluation_messages = [
                EvaluationMessage(
                    input=pair["human"],
                    output=pair["ai"],
                    context=pair["context"],
                    metadata={"created_mode": "manual"},
                )
                for pair in self.cleaned_data.get("message_pairs", [])
            ]

        if evaluation_messages:
            EvaluationMessage.objects.bulk_create(evaluation_messages)
            dataset.messages.set(evaluation_messages)

        return dataset


class EvaluationDatasetEditForm(forms.ModelForm):
    """Simple form for editing existing evaluation datasets (name only)."""

    class Meta:
        model = EvaluationDataset
        fields = ("name",)

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team

    def clean_name(self):
        name = self.cleaned_data.get("name")
        if name:
            duplicate_query = EvaluationDataset.objects.filter(name=name, team=self.team)
            if self.instance.pk:
                duplicate_query = duplicate_query.exclude(pk=self.instance.pk)

            if duplicate_query.exists():
                raise forms.ValidationError("A dataset with this name already exists in your team.")

        return name
