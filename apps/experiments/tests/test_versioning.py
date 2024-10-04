import pytest
from django.db import models

from apps.experiments.models import VersionsMixin
from apps.experiments.versioning import Version, VersionField, compare_models, differs
from apps.utils.models import BaseModel


@pytest.fixture()
def test_model():
    class TestModel(BaseModel, VersionsMixin):
        value = models.CharField()
        working_version = models.ForeignKey(
            "self",
            on_delete=models.CASCADE,
            null=True,
            blank=True,
            related_name="versions",
        )

    return TestModel


@pytest.mark.parametrize(
    ("value1", "value2", "exclude_fields", "changed_fields"),
    [
        ("1", "1", [], ["working_version_id"]),
        ("1", "1", VersionsMixin.DEFAULT_EXCLUDED_KEYS, []),
        ("1", "2", VersionsMixin.DEFAULT_EXCLUDED_KEYS, ["value"]),
    ],
)
def test_compare_models(value1, value2, exclude_fields, changed_fields, test_model):
    instance1 = test_model(value=value1, working_version_id=None)
    instance2 = test_model(value=value2, working_version_id=1)
    changed_fields = compare_models(instance1, instance2, exclude_fields=exclude_fields)
    assert changed_fields == set(changed_fields)


def test_differs(test_model):
    assert (
        differs(
            test_model(value="1", working_version_id=None),
            test_model(value="1", working_version_id=1),
            exclude_model_fields=VersionsMixin.DEFAULT_EXCLUDED_KEYS,
        )
        is False
    )
    assert (
        differs(
            test_model(value="1", working_version_id=None),
            test_model(value="2", working_version_id=1),
            exclude_model_fields=VersionsMixin.DEFAULT_EXCLUDED_KEYS,
        )
        is True
    )
    assert differs(1, 2) is True
    assert differs(True, False) is True


class TestVersion:
    def test_compare(self, test_model):
        instance1 = test_model(value="1", working_version_id=None)
        version1 = Version(
            instance=instance1,
            fields=[
                VersionField(group_name="G1", name="the_value", raw_value=instance1.value),
            ],
        )
        similar_instance = test_model(value="1", working_version_id=None)
        similar_version2 = Version(
            instance=similar_instance,
            fields=[
                VersionField(group_name="G1", name="the_value", raw_value=similar_instance.value),
            ],
        )
        different_instance = test_model(value="2", working_version_id=None)
        different_version2 = Version(
            instance=different_instance,
            fields=[
                VersionField(group_name="G1", name="the_value", raw_value=different_instance.value),
            ],
        )
        version1.compare(similar_version2)
        assert version1.fields_changed is False

        version1.compare(different_version2)
        assert version1.fields_changed is True

        changed_field = version1.fields[0]
        assert changed_field.name == "the_value"
        assert changed_field.label == "The Value"
        assert changed_field.raw_value == "1"
        assert changed_field.changed is True
        assert changed_field.previous_field_version.raw_value == "2"

    def test_type_error_raised(self, test_model):
        """A type error should be raised when comparing versions of differing types"""
        instance1 = test_model(value="1", working_version_id=None)
        version1 = Version(
            instance=instance1,
            fields=[],
        )

        version2 = Version(
            instance="String type",
            fields=[],
        )

        with pytest.raises(TypeError, match=r"Cannot compare instances of different types."):
            version1.compare(version2)
