import pytest

from apps.experiments.models import Experiment, VersionsMixin
from apps.experiments.versioning import Version, VersionField, compare_models, differs
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_compare_models():
    experiment = ExperimentFactory(temperature=0.1)
    instance1 = Experiment.objects.get(id=experiment.id)
    instance2 = Experiment.objects.get(id=experiment.id)
    assert compare_models(instance1, instance2, exclude_fields=[]) == set()
    instance2.temperature = 0.2
    assert compare_models(instance1, instance2, exclude_fields=["temperature"]) == set()
    assert compare_models(instance1, instance2, exclude_fields=[]) == set(["temperature"])


def test_differs():
    experiment1 = ExperimentFactory.build(temperature=0.1)
    experiment2 = ExperimentFactory.build(temperature=0.1)
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
