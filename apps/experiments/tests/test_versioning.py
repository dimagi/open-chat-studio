import pytest

from apps.experiments.models import Experiment, VersionsMixin
from apps.experiments.versioning import Version, VersionField, differs
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


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


class TestVersion:
    def test_compare(self):
        instance1 = ExperimentFactory.build(temperature=0.1)
        version1 = Version(
            instance=instance1,
            fields=[
                VersionField(group_name="G1", name="the_temperature", raw_value=instance1.temperature),
            ],
        )
        similar_instance = instance1
        similar_version2 = Version(
            instance=similar_instance,
            fields=[
                VersionField(group_name="G1", name="the_temperature", raw_value=similar_instance.temperature),
            ],
        )
        different_instance = ExperimentFactory.build(temperature=0.2)
        different_version2 = Version(
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

    @pytest.mark.django_db()
    def test_compare_querysets_with_equal_results(self):
        experiment = ExperimentFactory()
        queryset = Experiment.objects.filter(id=experiment.id)
        # Compare with itself
        version_field = VersionField(queryset=queryset)
        version_field._compare_queryset(queryset)
        assert version_field.changed is False
        assert len(version_field.queryset_result_versions) == 1
        queryset_result_version = version_field.queryset_result_versions[0]
        assert queryset_result_version.raw_value == experiment
        assert queryset_result_version.previous_field_version.raw_value == experiment

    @pytest.mark.django_db()
    def test_compare_querysets_with_results_of_differing_versions(self):
        experiment = ExperimentFactory()
        queryset = Experiment.objects.filter(id=experiment.id)
        # Compare with new version
        new_version = experiment.create_new_version()
        experiment.prompt_text = "This now changed"
        experiment.save()
        version_field = VersionField(queryset=queryset)
        version_field._compare_queryset(Experiment.objects.filter(id=new_version.id))
        assert version_field.changed is True
        assert len(version_field.queryset_result_versions) == 1
        queryset_result_version = version_field.queryset_result_versions[0]
        assert queryset_result_version.raw_value == experiment
        assert queryset_result_version.previous_field_version.raw_value == new_version

    @pytest.mark.django_db()
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
        version_field._compare_queryset(Experiment.objects.filter(id=another_experiment.id))
        assert version_field.changed is True

        assert len(version_field.queryset_result_versions) == 2
        first_result_version = version_field.queryset_result_versions[0]
        assert first_result_version.raw_value == experiment
        assert first_result_version.previous_field_version is None

        second_result_version = version_field.queryset_result_versions[1]
        assert second_result_version.raw_value is None
        assert second_result_version.previous_field_version.raw_value == another_experiment

    def test_type_error_raised(self):
        """A type error should be raised when comparing versions of differing types"""
        instance1 = ExperimentFactory.build()
        version1 = Version(
            instance=instance1,
            fields=[],
        )

        version2 = Version(
            instance=ExperimentSessionFactory.build(),
            fields=[],
        )

        with pytest.raises(TypeError, match=r"Cannot compare instances of different types."):
            version1.compare(version2)

    def test_fields_grouped(self, experiment):
        new_version = experiment.create_new_version()
        original_version = experiment.version
        original_version.compare(new_version.version)
        all_groups = set([field.group_name for field in experiment.version.fields])
        collected_group_names = []
        for group in original_version.fields_grouped:
            collected_group_names.append(group.name)
            assert group.has_changed_fields is False

        assert all_groups - set(collected_group_names) == set()

        # Let's change something
        new_version.temperature = new_version.temperature + 0.1

        original_version.compare(new_version.version)
        temerature_group_name = original_version.get_field("temperature").group_name
        # Find the temperature group and check that it reports a change
        for group in original_version.fields_grouped:
            if group.name == temerature_group_name:
                assert group.has_changed_fields is True

    @pytest.mark.django_db()
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
        assert version_field.is_queryset is True
        version_field._compare_queryset(previous_queryset)
        assert version_field.changed is True
