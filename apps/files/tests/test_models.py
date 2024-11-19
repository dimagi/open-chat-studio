import pytest

from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
@pytest.mark.parametrize("clear_external_id", [True, False])
def test_external_id_cleared_on_duplicate(clear_external_id):
    file = FileFactory(external_id="123")
    new_file = file.duplicate(clear_external_id=clear_external_id)
    assert new_file.external_id == "" if clear_external_id else "123"
