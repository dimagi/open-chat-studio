import pytest
from django.db import models

from apps.experiments.helpers import compare_models, differs
from apps.experiments.models import VersionsMixin
from apps.utils.models import BaseModel


class TestModel(BaseModel):
    value = models.CharField()
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )


@pytest.mark.parametrize(
    ("value1", "value2", "exclude_fields", "changed_fields"),
    [
        ("1", "1", [], ["working_version_id"]),
        ("1", "1", VersionsMixin.DEFAULT_EXCLUDED_KEYS, []),
        ("1", "2", VersionsMixin.DEFAULT_EXCLUDED_KEYS, ["value"]),
    ],
)
def test_compare_models(value1, value2, exclude_fields, changed_fields):
    instance1 = TestModel(value=value1, working_version_id=None)
    instance2 = TestModel(value=value2, working_version_id=1)
    changed_fields = compare_models(instance1, instance2, exclude_fields=exclude_fields)
    assert changed_fields == set(changed_fields)


def test_differs():
    assert (
        differs(
            TestModel(value="1", working_version_id=None),
            TestModel(value="1", working_version_id=1),
            exclude_model_fields=VersionsMixin.DEFAULT_EXCLUDED_KEYS,
        )
        is False
    )
    assert (
        differs(
            TestModel(value="1", working_version_id=None),
            TestModel(value="2", working_version_id=1),
            exclude_model_fields=VersionsMixin.DEFAULT_EXCLUDED_KEYS,
        )
        is True
    )
    assert differs(1, 2) is True
    assert differs(True, False) is True
