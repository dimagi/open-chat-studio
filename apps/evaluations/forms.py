import importlib
import json

from django import forms
from django.forms.widgets import RadioSelect
from pydantic import ValidationError as PydanticValidationError

from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
    Evaluator,
    ExperimentVersionSelection,
)
from apps.evaluations.utils import parse_csv_value_as_json, parse_history_text
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
        option["attrs"]["x-model"] = "mode"
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
        help_text="(Optional) Select the chatbot to run generation against",
        widget=forms.Select(attrs={"class": "select w-full"}),
        label="Chatbot",
    )
    run_generation = forms.BooleanField(
        required=False,
        initial=False,
        label="Run generation step before evaluation",
        widget=forms.CheckboxInput(attrs={"x-model": "runGeneration"}),
    )
    experiment_version = None  # Created dynamically based on the queryset

    class Meta:
        model = EvaluationConfig
        fields = [
            "name",
            "evaluators",
            "dataset",
            "experiment_version",
            "run_generation",
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
            self.initial["run_generation"] = self.instance.experiment_version or self.instance.base_experiment

            if self.instance.experiment_version:
                # For specific version, set experiment field based on the experiment_version
                if working_experiment := self.instance.experiment_version.get_working_version():
                    self.initial["experiment"] = working_experiment
                    # Filter the experiment_version queryset to only show versions for this experiment
                    experiment_version_queryset = self._get_version_choices(working_experiment.id)

            elif self.instance.base_experiment:
                # For sentinel values, set experiment and convert sentinel type to string
                self.initial["experiment"] = self.instance.base_experiment
                # Filter the experiment_version queryset to only show versions for this experiment
                experiment_version_queryset = self._get_version_choices(self.instance.base_experiment.id)
                if self.instance.version_selection_type == ExperimentVersionSelection.LATEST_WORKING:
                    self.initial["experiment_version"] = ExperimentVersionSelection.LATEST_WORKING.value
                elif self.instance.version_selection_type == ExperimentVersionSelection.LATEST_PUBLISHED:
                    self.initial["experiment_version"] = ExperimentVersionSelection.LATEST_PUBLISHED.value
        elif experiment_id := self.data.get("experiment"):
            experiment_version_queryset = self._get_version_choices(experiment_id)

        self.fields["experiment_version"] = ExperimentChoiceField(
            queryset=experiment_version_queryset,
            required=False,
            widget=forms.Select(attrs={"class": "select w-full"}),
            label="Chatbot version",
            help_text="Choose a chatbot version",
        )

    def _get_version_choices(self, experiment_id: int):
        """Get all versions for a specific experiment including working version"""
        return Experiment.objects.all_versions_queryset(experiment_id).filter(team=self.team)

    def clean(self):
        cleaned_data = super().clean()

        experiment_version = cleaned_data.get("experiment_version")
        experiment = cleaned_data.get("experiment")

        if cleaned_data.get("run_generation") and not experiment:
            self.add_error("experiment", "Please select a version")
        elif not cleaned_data.get("run_generation"):
            cleaned_data["experiment"] = experiment = None

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


class EvaluationDatasetBaseForm(forms.ModelForm):
    """Base form with common fields and methods for dataset forms."""

    MODE_CHOICES = [
        ("clone", "Clone from sessions"),
        ("manual", "Create manually"),
        ("csv", "Upload CSV file"),
    ]

    mode = forms.ChoiceField(choices=MODE_CHOICES, widget=StyledRadioSelect(), label="Dataset creation mode")

    session_ids = forms.CharField(
        widget=forms.HiddenInput(attrs={"x-ref": "sessionIds"}),
        required=False,
    )

    filtered_session_ids = forms.CharField(
        widget=forms.HiddenInput(attrs={"x-ref": "filteredSessionIds"}),
        required=False,
    )

    class Meta:
        model = EvaluationDataset
        fields = ("name",)

    def __init__(self, team, *args, **kwargs):
        self.filter_params = kwargs.pop("filter_params", None)
        self.timezone = kwargs.pop("timezone", None)
        super().__init__(*args, **kwargs)
        self.team = team

    def _clean_clone(self):
        """Validates session IDs for clone mode. Returns (session_ids, filtered_session_ids)."""
        session_ids_str = self.data.get("session_ids", "")
        session_ids = {sid for sid in session_ids_str.split(",") if sid}

        filtered_session_ids_str = self.data.get("filtered_session_ids", "")
        filtered_session_ids = {sid for sid in filtered_session_ids_str.split(",") if sid}

        if not session_ids and not filtered_session_ids:
            raise forms.ValidationError("At least one session must be selected when cloning from sessions.")

        intersection = session_ids & filtered_session_ids
        if intersection:
            raise forms.ValidationError(
                "A session cannot be selected in both 'All Messages' and 'Filtered Messages'. "
                f"The following sessions are in both lists: {', '.join(sorted(str(sid) for sid in intersection))}"
            )

        all_session_ids = session_ids.union(filtered_session_ids)
        existing_sessions = ExperimentSession.objects.filter(
            team=self.team, external_id__in=all_session_ids
        ).values_list("external_id", flat=True)

        missing_sessions = all_session_ids - set(str(sid) for sid in existing_sessions)
        if missing_sessions:
            raise forms.ValidationError(
                "The following sessions do not exist or you don't have permission to access them: "
                f"{', '.join(sorted(missing_sessions))}"
            )

        return session_ids, filtered_session_ids

    def _create_messages_from_sessions(self, session_ids, filtered_session_ids):
        """Creates EvaluationMessage objects from sessions."""
        return EvaluationMessage.create_from_sessions(
            team=self.team,
            external_session_ids=session_ids,
            filtered_session_ids=filtered_session_ids,
            filter_params=self.filter_params,
            timezone=self.timezone,
        )


class EvaluationDatasetForm(EvaluationDatasetBaseForm):
    """Form for creating evaluation datasets."""

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

    def __init__(self, team, *args, **kwargs):
        super().__init__(team, *args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data.get("name")
        if name and EvaluationDataset.objects.filter(name=name, team=self.team).exists():
            raise forms.ValidationError("A dataset with this name already exists in your team.")

        return name

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("mode")
        if mode == "clone":
            session_ids, filtered_session_ids = self._clean_clone()
            cleaned_data["session_ids"] = session_ids
            cleaned_data["filtered_session_ids"] = filtered_session_ids
        elif mode == "manual":
            cleaned_data["message_pairs"] = self._clean_manual()
        elif mode == "csv":
            csv_data, column_mapping, history_column = self._clean_csv()
            cleaned_data["csv_data"] = csv_data
            cleaned_data["column_mapping"] = column_mapping
            cleaned_data["history_column"] = history_column
        return cleaned_data

    def _clean_manual(self):
        messages_json = self.data.get("messages_json", "")
        if not messages_json:
            raise forms.ValidationError("At least one message pair must be added when creating manually.")

        message_pairs = _clean_json_field("Message data", messages_json)

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

            context = _get_message_pair_value("Context", i, pair.get("context"))
            participant_data = _get_message_pair_value("Participant data", i, pair.get("participant_data"))
            session_state = _get_message_pair_value("Session state", i, pair.get("session_state"))

            # Parse history text if provided
            history = []
            if history_text := pair.get("history_text", "").strip():
                try:
                    history = parse_history_text(history_text)
                except Exception as err:
                    raise forms.ValidationError(f"History for pair {i + 1} has invalid format") from err

            validated_pairs.append(
                {
                    "human": EvaluationMessageContent(content=pair.get("human", "").strip(), role="human").model_dump(),
                    "ai": EvaluationMessageContent(content=pair.get("ai", "").strip(), role="ai").model_dump(),
                    "context": context,
                    "history": history,
                    "participant_data": participant_data,
                    "session_state": session_state,
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

        mapped_columns = set()
        if input_col := column_mapping.get("input"):
            mapped_columns.add(input_col)
        if output_col := column_mapping.get("output"):
            mapped_columns.add(output_col)

        for field_type in ["context", "participant_data", "session_state"]:
            if field_mapping := column_mapping.get(field_type):
                if isinstance(field_mapping, dict):
                    mapped_columns.update(field_mapping.values())

        if history_column:
            mapped_columns.add(history_column)

        missing_columns = mapped_columns - csv_columns
        if missing_columns:
            raise forms.ValidationError(f"Columns not found in CSV file: {', '.join(sorted(missing_columns))}")

        # Validate field names for all nested structures
        field_type_labels = {
            "context": "Context",
            "participant_data": "Participant data",
            "session_state": "Session state",
        }
        for field_type in ["context", "participant_data", "session_state"]:
            if field_mapping := column_mapping.get(field_type):
                for field_name in field_mapping:
                    if not self._is_valid_python_identifier(field_name):
                        label = field_type_labels.get(field_type, field_type)
                        raise forms.ValidationError(
                            f"{label} field '{field_name}' is not a valid Python identifier. "
                            "Field names must start with a letter or underscore and contain only letters, digits, "
                            "and underscores."
                        )

        valid_rows = 0
        for i, row in enumerate(csv_data):
            input_content = row.get(column_mapping.get("input", ""), "").strip()
            output_content = row.get(column_mapping.get("output", ""), "").strip()
            if input_content and output_content:
                valid_rows += 1

            if history_column and history_column in row:
                history_text = row[history_column].strip()
                if history_text:
                    try:
                        parse_history_text(history_text)
                    except HistoryParseException as exc:
                        raise forms.ValidationError(f"Invalid history in row: {i}") from exc

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
        session_ids = self.cleaned_data.get("session_ids", [])
        filtered_session_ids = self.cleaned_data.get("filtered_session_ids", [])

        if session_ids or filtered_session_ids:
            return self._create_messages_from_sessions(session_ids, filtered_session_ids)
        return []

    def _save_manual(self):
        return [
            EvaluationMessage(
                input=pair["human"],
                output=pair["ai"],
                context=pair["context"],
                history=pair.get("history", []),
                participant_data=pair.get("participant_data", {}),
                session_state=pair.get("session_state", {}),
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
            if context_mapping := column_mapping.get("context"):
                for field_name, csv_column in context_mapping.items():
                    if csv_column in row:
                        context[field_name] = parse_csv_value_as_json(row[csv_column])

            participant_data = {}
            if participant_data_mapping := column_mapping.get("participant_data"):
                for field_name, csv_column in participant_data_mapping.items():
                    if csv_column in row:
                        participant_data[field_name] = parse_csv_value_as_json(row[csv_column])

            session_state = {}
            if session_state_mapping := column_mapping.get("session_state"):
                for field_name, csv_column in session_state_mapping.items():
                    if csv_column in row:
                        session_state[field_name] = parse_csv_value_as_json(row[csv_column])

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
                    participant_data=participant_data,
                    session_state=session_state,
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


def _get_message_pair_value(field_name: str, pair_index: int, field_value: str) -> dict:
    return _clean_json_field(f"{field_name} for pair {pair_index + 1}", field_value)


def _clean_json_field(field_name: str, field_value: str) -> dict:
    if not field_value.strip():
        return {}
    try:
        return json.loads(field_value)
    except json.JSONDecodeError as err:
        raise forms.ValidationError(f"{field_name} is not valid JSON") from err


class EvaluationDatasetEditForm(EvaluationDatasetBaseForm):
    """Form for editing existing evaluation datasets."""

    def __init__(self, team, *args, **kwargs):
        super().__init__(team, *args, **kwargs)
        self.fields["mode"].label = "Add messages mode"

    def clean_name(self):
        name = self.cleaned_data.get("name")
        if name:
            duplicate_query = EvaluationDataset.objects.filter(name=name, team=self.team)
            if self.instance.pk:
                duplicate_query = duplicate_query.exclude(pk=self.instance.pk)

            if duplicate_query.exists():
                raise forms.ValidationError("A dataset with this name already exists in your team.")

        return name

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("mode")

        if mode == "clone":
            session_ids, filtered_session_ids = self._clean_clone()
            cleaned_data["session_ids"] = session_ids
            cleaned_data["filtered_session_ids"] = filtered_session_ids

        return cleaned_data

    def clone_messages_to_dataset(self, dataset: EvaluationDataset) -> int:
        """
        Clone messages from sessions into the existing dataset, avoiding duplicates.
        Uses ChatMessage IDs for duplicate detection.
        """
        session_ids = self.cleaned_data.get("session_ids", set())
        filtered_session_ids = self.cleaned_data.get("filtered_session_ids", set())

        if not session_ids and not filtered_session_ids:
            return 0

        new_evaluation_messages = self._create_messages_from_sessions(session_ids, filtered_session_ids)
        existing_chat_message_pairs = set(
            dataset.messages.filter(
                input_chat_message_id__isnull=False,
                expected_output_chat_message_id__isnull=False,
            ).values_list("input_chat_message_id", "expected_output_chat_message_id")
        )

        # Filter out duplicates based on ChatMessage IDs
        messages_to_add = []
        for msg in new_evaluation_messages:
            chat_pair = (msg.input_chat_message_id, msg.expected_output_chat_message_id)
            if chat_pair not in existing_chat_message_pairs:
                messages_to_add.append(msg)
                existing_chat_message_pairs.add(chat_pair)

        if messages_to_add:
            created_messages = EvaluationMessage.objects.bulk_create(messages_to_add)
            dataset.messages.add(*created_messages)

        return len(messages_to_add)
