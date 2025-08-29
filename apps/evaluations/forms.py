import importlib
import json

from django import forms
from django.forms.widgets import RadioSelect
from pydantic import ValidationError as PydanticValidationError

from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
    Evaluator,
    ExperimentVersionSelection,
)
from apps.evaluations.utils import parse_history_text
from apps.experiments.models import Experiment, ExperimentSession


class StyledRadioSelect(RadioSelect):
    def __init__(self, attrs=None):
        default_attrs = {"class": "space-y-1"}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)

        option["attrs"]["class"] = "radio radio-primary mr-2"
        return option


def get_experiment_version_choices(experiment_queryset):
    """
    Get experiment version choices including sentinel values and specific versions.
    Returns consistent (value, label) tuples that can be used by both forms and views.
    """
    choices = [
        (ExperimentVersionSelection.LATEST_WORKING.value, ExperimentVersionSelection.LATEST_WORKING.label),
        (ExperimentVersionSelection.LATEST_PUBLISHED.value, ExperimentVersionSelection.LATEST_PUBLISHED.label),
    ]

    # Add specific versions if queryset provided
    if experiment_queryset is not None:
        for version in experiment_queryset:
            label = str(version)
            if version.working_version_id is None:  # This is the working version
                continue  # Ignore it as we have "LATEST_WORKING" as a special value
            elif version.is_default_version:  # This is the default published version
                label = f"{label} (Current published version)"

            choices.append((version.id, label))

    return choices


class ExperimentChoiceField(forms.ChoiceField):
    def __init__(self, queryset, *args, **kwargs):
        self.queryset = queryset
        kwargs["choices"] = get_experiment_version_choices(queryset)
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
        self.fields["experiment"].queryset = (
            Experiment.objects.working_versions_queryset().filter(team=team).order_by("name")
        )

        experiment_version_queryset = None

        if self.instance and self.instance.pk:
            if self.instance.experiment_version:
                # For specific version, set experiment field based on the experiment_version
                if working_experiment := self.instance.experiment_version.get_working_version():
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
        return Experiment.objects.all_versions_queryset(experiment).filter(team=self.team)

    def clean(self):
        cleaned_data = super().clean()

        experiment_version = cleaned_data.get("experiment_version")
        experiment = cleaned_data.get("experiment")

        if experiment and not experiment_version:
            self.add_error("experiment_version", "Please select a version")
            return cleaned_data

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
        ("csv", "Upload CSV file"),
    ]

    mode = forms.ChoiceField(
        choices=MODE_CHOICES, widget=StyledRadioSelect(), initial="clone", label="Dataset creation mode"
    )

    session_ids = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    messages_json = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    column_mapping = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    csv_data = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    populate_history = forms.BooleanField(
        required=False,
        initial=False,
        label="Automatically populate history",
        help_text="When enabled, each message will include the conversation history from previous rows in the CSV.",
    )

    history_column = forms.CharField(
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
            cleaned_data["session_ids"] = self._clean_clone()
        elif mode == "manual":
            cleaned_data["message_pairs"] = self._clean_manual()
        elif mode == "csv":
            csv_data, column_mapping, history_column = self._clean_csv()
            cleaned_data["csv_data"] = csv_data
            cleaned_data["column_mapping"] = column_mapping
            cleaned_data["history_column"] = history_column
        return cleaned_data

    def _clean_clone(self):
        session_ids_str = self.data.get("session_ids", "")
        session_ids = set(session_ids_str.split(","))
        session_ids.discard("")  # Remove empty strings

        if not session_ids:
            raise forms.ValidationError("At least one session must be selected when cloning from sessions.")

        existing_sessions = ExperimentSession.objects.filter(team=self.team, external_id__in=session_ids).values_list(
            "external_id", flat=True
        )

        missing_sessions = set(session_ids) - set(existing_sessions)
        if missing_sessions:
            raise forms.ValidationError(
                "The following sessions do not exist or you don't have permission to access them: "
                f"{', '.join(missing_sessions)}"
            )

        return session_ids

    def _clean_manual(self):
        message_pairs = []
        messages_json = self.data.get("messages_json", "")
        if not messages_json:
            raise forms.ValidationError("At least one message pair must be added when creating manually.")

        try:
            message_pairs = json.loads(messages_json)
        except json.JSONDecodeError as err:
            raise forms.ValidationError("Messages data is invalid JSON.") from err

        if not isinstance(message_pairs, list) or len(message_pairs) == 0:
            raise forms.ValidationError("At least one message pair must be added.")

        validated_pairs = []
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

            # Parse history text if provided
            history = []
            history_text = pair.get("history_text", "").strip()
            if history_text:
                try:
                    history = parse_history_text(history_text)
                except Exception as err:
                    raise forms.ValidationError(f"History for pair {i + 1} has invalid format") from err

                if not history:
                    # There was history text, but nothing came out after parsing
                    raise forms.ValidationError(f"History for pair {i + 1} has invalid format")

            validated_pairs.append(
                {
                    "human": EvaluationMessageContent(content=pair.get("human", "").strip(), role="human").model_dump(),
                    "ai": EvaluationMessageContent(content=pair.get("ai", "").strip(), role="ai").model_dump(),
                    "context": json.loads(pair.get("context", "{}")) if pair.get("context") else {},
                    "history": history,
                }
            )
        return validated_pairs

    def _clean_csv(self):
        column_mapping_str = self.data.get("column_mapping", "")
        csv_data_str = self.data.get("csv_data", "")
        history_column = self.data.get("history_column", "").strip()
        populate_history = self.data.get("populate_history") == "on"

        if not csv_data_str:
            raise forms.ValidationError("Please upload a CSV file.")
        column_mapping = {}
        if column_mapping_str:
            try:
                column_mapping = json.loads(column_mapping_str)
            except json.JSONDecodeError as err:
                raise forms.ValidationError("Invalid column mapping data.") from err
        try:
            csv_data = json.loads(csv_data_str)
        except json.JSONDecodeError as err:
            raise forms.ValidationError("Invalid CSV data.") from err

        if not csv_data:
            raise forms.ValidationError("CSV data appears to be empty or invalid.")

        if not column_mapping.get("input") or not column_mapping.get("output"):
            raise forms.ValidationError("Both input and output columns must be mapped.")

        if populate_history and history_column:
            raise forms.ValidationError(
                "Cannot both automatically populate history and use a history column. Please choose one option."
            )

        csv_columns = set(csv_data[0].keys()) if csv_data else set()
        mapped_columns = {col for col in column_mapping.values() if col}

        if history_column:
            mapped_columns.add(history_column)

        missing_columns = mapped_columns - csv_columns
        if missing_columns:
            raise forms.ValidationError(f"Columns not found in CSV file: {', '.join(sorted(missing_columns))}")

        for field_name in column_mapping:
            if field_name not in ["input", "output"]:
                if not self._is_valid_python_identifier(field_name):
                    raise forms.ValidationError(
                        f"Context field '{field_name}' is not a valid Python identifier. "
                        "Field names must start with a letter or underscore and contain only letters, digits, "
                        "and underscores."
                    )

        valid_rows = 0
        for row in csv_data:
            input_content = row.get(column_mapping.get("input", ""), "").strip()
            output_content = row.get(column_mapping.get("output", ""), "").strip()
            if input_content and output_content:
                valid_rows += 1

        if valid_rows == 0:
            raise forms.ValidationError("No valid message pairs found in CSV data.")

        return csv_data, column_mapping, history_column

    def _is_valid_python_identifier(self, name):
        """Check if a string is a valid Python identifier."""
        if not name:
            return False
        return name.isidentifier()

    def save(self, commit=True):
        """Create dataset based on the selected mode."""
        dataset = super().save(commit=False)

        if not commit:
            return dataset

        dataset.save()

        mode = self.cleaned_data.get("mode")
        evaluation_messages = []

        if mode == "clone":
            evaluation_messages = self._save_clone()
        elif mode == "manual":
            evaluation_messages = self._save_manual()
        elif mode == "csv":
            evaluation_messages = self._save_csv()
        if evaluation_messages:
            EvaluationMessage.objects.bulk_create(evaluation_messages)
            dataset.messages.set(evaluation_messages)

        return dataset

    def _save_clone(self):
        evaluation_messages = []
        session_ids = self.cleaned_data.get("session_ids", [])
        if session_ids:
            evaluation_messages = EvaluationMessage.create_from_sessions(self.team, session_ids)
        return evaluation_messages

    def _save_manual(self):
        return [
            EvaluationMessage(
                input=pair["human"],
                output=pair["ai"],
                context=pair["context"],
                history=pair.get("history", []),
                metadata={"created_mode": "manual"},
            )
            for pair in self.cleaned_data.get("message_pairs", [])
        ]

    def _save_csv(self):
        csv_data = self.cleaned_data.get("csv_data", [])
        column_mapping = self.cleaned_data.get("column_mapping", {})
        populate_history = self.cleaned_data.get("populate_history", False)
        history_column = self.cleaned_data.get("history_column", "")

        evaluation_messages = []
        auto_history = []  # For auto-populate mode

        for row in csv_data:
            # Extract mapped columns
            input_content = row.get(column_mapping.get("input", ""), "").strip()
            output_content = row.get(column_mapping.get("output", ""), "").strip()
            if not input_content or not output_content:
                continue

            context = {}
            for field_name, csv_column in column_mapping.items():
                if field_name not in ["input", "output"] and csv_column in row:
                    context[field_name] = row[csv_column]

            message_history = []
            if populate_history:
                # Use auto-populated history from previous messages
                message_history = [msg.copy() for msg in auto_history]
            elif history_column and history_column in row:
                # Parse history from CSV column
                history_text = row[history_column].strip()
                if history_text:
                    message_history = parse_history_text(history_text)

            evaluation_messages.append(
                EvaluationMessage(
                    input=EvaluationMessageContent(content=input_content, role="human").model_dump(),
                    output=EvaluationMessageContent(content=output_content, role="ai").model_dump(),
                    context=context,
                    history=message_history,
                    metadata={"created_mode": "csv"},
                )
            )

            if populate_history:
                auto_history.append(
                    {
                        "message_type": "HUMAN",
                        "content": input_content.strip(),
                        "summary": None,
                    }
                )
                auto_history.append(
                    {
                        "message_type": "AI",
                        "content": output_content.strip(),
                        "summary": None,
                    }
                )
        return evaluation_messages


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
