import pytest
import yaml
from django.core.management import call_command
from django.test import Client


def test_schema_filters():
    c = Client()
    response = c.get("/api/schema/")
    response_yaml = response.content.decode("utf-8")
    assert "/cms/" not in response_yaml


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_schema_is_up_to_date_and_valid(pytestconfig, tmp_path, version):
    """If this test fails run `inv schema` to update the schema."""
    path = tmp_path / f"{version}.yml"
    call_command("spectacular", api_version=version, validate=True, file=str(path))
    with open(path) as f:
        new_schema = yaml.safe_load(f)

    with open(f"{pytestconfig.rootdir}/api-schemas/{version}.yml") as f:
        old_schema = yaml.safe_load(f)

    assert old_schema == new_schema
