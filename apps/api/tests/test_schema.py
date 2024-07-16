import yaml
from django.core.management import call_command
from django.test import Client


def test_schema_filters():
    c = Client()
    response = c.get("/api/schema/")
    response_yaml = response.content.decode("utf-8")
    assert "/cms/" not in response_yaml


def test_schema_is_up_to_date_and_valid(pytestconfig):
    """If this test fails run `./manage.py spectacular --file api-schema.yml --validate` to update the schema."""
    path = f"{pytestconfig.rootdir}/tmp_schema.yml"
    call_command("spectacular", validate=True, file=path)
    with open(path) as f:
        new_schema = yaml.safe_load(f)

    with open(f"{pytestconfig.rootdir}/api-schema.yml") as f:
        old_schema = yaml.safe_load(f)

    assert old_schema == new_schema
