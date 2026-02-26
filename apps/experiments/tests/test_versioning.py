import pytest

from apps.experiments.models import Experiment
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, differs
from apps.files.models import File
from apps.utils.factories.events import EventActionFactory, EventActionType, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, SourceMaterialFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import TraceProviderFactory


@pytest.mark.django_db()
def test_compare_models():
    experiment = ExperimentFactory(temperature=0.1)
    instance1 = Experiment.objects.get(id=experiment.id)
    instance2 = Experiment.objects.get(id=experiment.id)
    assert instance1.compare_with_model(instance2, exclude_fields=[]) == set()
    instance2.temperature = 0.2
    assert instance1.compare_with_model(instance2, exclude_fields=["temperature"]) == set()
    assert instance1.compare_with_model(instance2, exclude_fields=[]) == set(["temperature"])


@pytest.mark.django_db()
def test_differs():
    experiment1 = ExperimentFactory(temperature=0.1)
    experiment2 = ExperimentFactory(temperature=0.1)
    assert (
        differs(
            experiment1,
            experiment1,
            exclude_model_fields=VersionsMixin.DEFAULT_EXCLUDED_KEYS,
        )
        is False
    )
    assert (
        differs(
            experiment1,
            experiment2,
            exclude_model_fields=VersionsMixin.DEFAULT_EXCLUDED_KEYS,
        )
        is True
    )
    assert differs(1, 2) is True
    assert differs(True, False) is True


@pytest.mark.django_db()
class TestVersion:
    def test_compare(self):
        instance1 = ExperimentFactory.build(temperature=0.1)
        version1 = VersionDetails(
            instance=instance1,
            fields=[
                VersionField(group_name="G1", name="the_temperature", raw_value=instance1.temperature),
            ],
        )
        similar_instance = instance1
        similar_version2 = VersionDetails(
            instance=similar_instance,
            fields=[
                VersionField(group_name="G1", name="the_temperature", raw_value=similar_instance.temperature),
            ],
        )
        different_instance = ExperimentFactory.build(temperature=0.2)
        different_version2 = VersionDetails(
            instance=different_instance,
            fields=[
                VersionField(group_name="G1", name="the_temperature", raw_value=different_instance.temperature),
            ],
        )
        version1.compare(similar_version2)
        assert version1.fields_changed is False

        version1.compare(different_version2)
        assert version1.fields_changed is True

        changed_field = version1.fields[0]
        assert changed_field.name == "the_temperature"
        assert changed_field.label == "The Temperature"
        assert changed_field.raw_value == 0.1
        assert changed_field.changed is True
        assert changed_field.previous_field_version.raw_value == 0.2

    def test_early_abort(self):
        experiment = ExperimentFactory(name="One", temperature=0.1)
        exp_version = experiment.create_new_version()

        experiment.name = "Two"
        experiment.seed_message = "new seed message"
        experiment.save()

        working_version = experiment.version_details
        version_version = exp_version.version_details

        working_version.compare(version_version)
        changed_fields = [field.name for field in working_version.fields if field.changed]
        assert len(changed_fields) == 2

        # Early abort should only detect one change
        experiment._clear_version_cache()
        exp_version._clear_version_cache()
        working_version = experiment.version_details
        version_version = exp_version.version_details
        working_version.compare(version_version, early_abort=True)
        changed_fields = [field.name for field in working_version.fields if field.changed]
        assert len(changed_fields) == 1

    def test_compare_querysets_with_equal_results(self):
        experiment = ExperimentFactory()
        queryset = Experiment.objects.filter(id=experiment.id)
        # Compare with itself
        version_field = VersionField(queryset=queryset)
        version_field.previous_field_version = VersionField(queryset=queryset)
        version_field._compare_querysets(queryset)
        assert version_field.changed is False
        assert len(version_field.queryset_results) == 1
        queryset_result_version = version_field.queryset_results[0]
        assert queryset_result_version.raw_value == experiment
        assert queryset_result_version.previous_field_version.raw_value == experiment

    def test_compare_querysets_with_results_of_differing_versions(self):
        experiment = ExperimentFactory()
        queryset = Experiment.objects.filter(id=experiment.id)
        # Compare with new version
        new_version = experiment.create_new_version()
        experiment.seed_message = "This now changed"
        experiment.save()
        version_field = VersionField(queryset=queryset)
        version_field.previous_field_version = VersionField(queryset=Experiment.objects.filter(id=new_version.id))
        version_field._compare_querysets()
        assert version_field.changed is True
        assert len(version_field.queryset_results) == 1
        queryset_result_version = version_field.queryset_results[0]
        assert queryset_result_version.raw_value == experiment
        assert queryset_result_version.previous_field_version.raw_value == new_version

    def test_compare_querysets_with_different_results(self):
        """
        When comparing different querysets, we expect two result versions to be created. One for the current queryset
        not having a match in the previous queryset and one for the previous queryset not having a match in the current
        queryset
        """
        experiment = ExperimentFactory()
        queryset = Experiment.objects.filter(id=experiment.id)
        # Compare with a totally different queryset
        another_experiment = ExperimentFactory()
        version_field = VersionField(queryset=queryset)
        version_field.previous_field_version = VersionField(
            queryset=Experiment.objects.filter(id=another_experiment.id)
        )
        version_field._compare_querysets()
        assert version_field.changed is True

        assert len(version_field.queryset_results) == 2
        first_result_version = version_field.queryset_results[0]
        assert first_result_version.raw_value == experiment
        assert first_result_version.previous_field_version is None

        second_result_version = version_field.queryset_results[1]
        assert second_result_version.raw_value is None
        assert second_result_version.previous_field_version.raw_value == another_experiment

    def test_type_error_raised(self):
        """A type error should be raised when comparing versions of differing types"""
        instance1 = ExperimentFactory.build()
        version1 = VersionDetails(
            instance=instance1,
            fields=[],
        )

        version2 = VersionDetails(
            instance=ExperimentSessionFactory.build(),
            fields=[],
        )

        with pytest.raises(TypeError, match=r"Cannot compare instances of different types."):
            version1.compare(version2)

    def test_fields_grouped(self, experiment):
        new_version = experiment.create_new_version()
        experiment._clear_version_cache()
        original_version = experiment.version_details
        original_version.compare(new_version.version_details)
        all_groups = set([field.group_name for field in experiment.version_details.fields])
        collected_group_names = []
        for group in original_version.fields_grouped:
            collected_group_names.append(group.name)
            assert group.has_changed_fields is False

        assert all_groups - set(collected_group_names) == set()

        # Let's change something
        new_version.seed_message = "new seed message"

        new_version._clear_version_cache()
        original_version.compare(new_version.version_details)
        seed_message_group_name = original_version.get_field("seed_message").group_name
        # Find the temperature group and check that it reports a change
        for group in original_version.fields_grouped:
            if group.name == seed_message_group_name:
                assert group.has_changed_fields is True

    def test_new_queryset_is_empty(self):
        """This tests the case where a queryset's previous results are not empty, but the current results are"""
        # Let's use experiment sessions as an example
        experiment = ExperimentFactory()
        ExperimentSessionFactory(experiment=experiment)
        previous_queryset = experiment.sessions
        # Compare with a totally different queryset
        new_experiment = ExperimentFactory()
        new_queryset = new_experiment.sessions
        # sanity check
        assert new_queryset.count() == 0

        version_field = VersionField(queryset=new_queryset)
        # another sanity check
        assert version_field.queryset is not None
        version_field.previous_field_version = VersionField(queryset=previous_queryset)
        version_field._compare_querysets()
        assert version_field.changed is True

    def test_compare_unversioned_models(self):
        trace_provider = TraceProviderFactory()
        experiment = ExperimentFactory()
        experiment_version = experiment.create_new_version()
        experiment.trace_provider = trace_provider
        experiment.save()
        experiment.version_details.compare(experiment_version.version_details)

    def test_action_params_expanded_into_fields(self):
        """
        Non-model fields that are considered part of a version (e.g. static trigger action params) are be expanded
        into separate versioned fields. If some of those parameters are removed in a new version, they should still
        show up as a versioned field in `version_details`, but only with an empty value.
        """
        experiment = ExperimentFactory()
        first_version_params = {"pipeline_id": 1}
        start_pipeline_action = EventActionFactory(
            action_type=EventActionType.PIPELINE_START,
            params=first_version_params,
        )
        static_trigger = StaticTriggerFactory(experiment=experiment, action=start_pipeline_action)
        experiment.create_new_version()

        # Now change the params
        action = static_trigger.action
        action.params = {"some_other_param": "a value"}  # ty: ignore[invalid-assignment]
        action.save()

        curr_version_details = static_trigger.version_details
        # Since the params changed, we expect pipeline_id to be missing from the version details
        assert "pipeline_id" not in [f.name for f in curr_version_details.fields]
        curr_version_details.compare(static_trigger.latest_version.version_details)
        # We expect the missing field(s) from the previous version details to be added to the current version details
        assert "pipeline_id" in [f.name for f in curr_version_details.fields]
        # Since the field is missing, the value should be None
        assert curr_version_details.get_field("pipeline_id").raw_value is None

    def test_queryset_results_for_display(self):
        """
        Test that when a VersionField has a queryset with more results than the display limit, only a limited number of
        results are available at the queryset_results_for_display attribute and that it is sorted with those that
        changed first.
        """
        FileFactory.create_batch(12)
        queryset = File.objects.all()

        field1 = VersionField(queryset=queryset)
        field2 = VersionField(queryset=queryset[:11])

        field1.compare(field2)
        # We expect NUM_QUERYSET_RESULTS_TO_SHOW results to be available
        assert len(field1.queryset_results_for_display) == VersionField.NUM_QUERYSET_RESULTS_TO_SHOW

        # Ensure it is sorted
        assert field1.queryset_results_for_display[0].changed
        for result in field1.queryset_results_for_display[1:]:
            assert result.changed is False


@pytest.mark.django_db()
class TestCopyExperiment:
    def test_basic_copy(self):
        experiment = ExperimentFactory(version_number=3)
        experiment_copy = experiment.create_new_version(name="test copy", is_copy=True)
        assert experiment_copy.id != experiment.id
        assert experiment_copy.public_id != experiment.public_id
        assert experiment_copy.name == "test copy"
        assert experiment_copy.version_number == 1
        assert experiment.version_number == 3
        assert experiment_copy.working_version_id is None

        assert experiment_copy.consent_form == experiment.consent_form
        assert experiment_copy.consent_form.is_working_version

        assert experiment_copy.pre_survey == experiment.pre_survey
        assert experiment_copy.pre_survey.is_working_version

        assert experiment_copy.synthetic_voice_id == experiment.synthetic_voice_id
        assert experiment_copy.voice_provider_id == experiment.voice_provider_id

    def test_related_models(self, team):
        source_material = SourceMaterialFactory()
        experiment = ExperimentFactory(team=team, source_material=source_material)

        static_trigger = StaticTriggerFactory(experiment=experiment)
        timeout_trigger = TimeoutTriggerFactory(experiment=experiment)

        experiment_copy = experiment.create_new_version(is_copy=True)
        assert experiment_copy.source_material == source_material

        assert experiment_copy.static_triggers.count() == 1
        static_trigger_copy = experiment_copy.static_triggers.first()
        assert static_trigger_copy != static_trigger
        assert static_trigger_copy.is_working_version
        assert static_trigger_copy.action != static_trigger.action

        assert experiment_copy.timeout_triggers.count() == 1
        timeout_trigger_copy = experiment_copy.timeout_triggers.first()
        assert timeout_trigger_copy != timeout_trigger
        assert timeout_trigger_copy.is_working_version
        assert timeout_trigger_copy.action != timeout_trigger.action

    def test_copy_pipeline(self):
        pipeline_data = {
            "edges": [
                {
                    "id": "start->render",
                    "source": "start",
                    "target": "render",
                },
                {
                    "id": "render->end",
                    "source": "render",
                    "target": "end",
                },
            ],
            "nodes": [
                {
                    "id": "start",
                    "data": {
                        "id": "start",
                        "type": "StartNode",
                    },
                },
                {
                    "id": "render",
                    "data": {
                        "id": "render",
                        "type": "RenderTemplate",
                        "params": {
                            "name": "render template",
                            "template_string": "{{input}}",
                        },
                    },
                    "type": "pipelineNode",
                    "position": {"x": 1086.2033684962435, "y": 91.8445271200375},
                },
                {
                    "id": "end",
                    "data": {
                        "id": "end",
                        "type": "EndNode",
                    },
                },
            ],
            "errors": {"test": "value"},
            "viewport": {"x": 235.23538305148782, "y": 365.64304629840245, "zoom": 0.5570968254096753},
        }
        pipeline = PipelineFactory(data=pipeline_data)
        experiment = ExperimentFactory(team=pipeline.team, pipeline=pipeline)

        experiment_copy = experiment.create_new_version(is_copy=True)
        assert experiment_copy.pipeline != pipeline
        assert experiment_copy.pipeline.is_working_version
        assert experiment_copy.pipeline.name == experiment_copy.name
        assert experiment_copy.pipeline.node_set.count() == 3
        node_ids = {node.type: node.flow_id for node in experiment_copy.pipeline.node_set.all()}
        assert experiment_copy.pipeline.data != pipeline_data
        assert experiment_copy.pipeline.data == {
            "edges": [
                {
                    "id": "start->render",
                    "source": node_ids["StartNode"],
                    "target": node_ids["RenderTemplate"],
                },
                {
                    "id": "render->end",
                    "source": node_ids["RenderTemplate"],
                    "target": node_ids["EndNode"],
                },
            ],
            "nodes": [
                {
                    "id": node_ids["StartNode"],
                    "data": {
                        "id": node_ids["StartNode"],
                        "type": "StartNode",
                    },
                },
                {
                    "id": node_ids["RenderTemplate"],
                    "data": {
                        "id": node_ids["RenderTemplate"],
                        "type": "RenderTemplate",
                        "params": {
                            "name": "render template",
                            "template_string": "{{input}}",
                        },
                    },
                    "type": "pipelineNode",
                    "position": {"x": 1086.2033684962435, "y": 91.8445271200375},
                },
                {
                    "id": node_ids["EndNode"],
                    "data": {
                        "id": node_ids["EndNode"],
                        "type": "EndNode",
                    },
                },
            ],
            "errors": {"test": "value"},
            "viewport": {"x": 235.23538305148782, "y": 365.64304629840245, "zoom": 0.5570968254096753},
        }
