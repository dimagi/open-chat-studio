import pytest
from django.db import models

from apps.experiments.models import VersionsMixin
from apps.experiments.versioning import compare_models, differs
from apps.utils.models import BaseModel


@pytest.fixture()
def test_model():
    class TestModel(BaseModel):
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
