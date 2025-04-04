import pytest

from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.files import FileFactory


@pytest.mark.django_db()
class TestNode:
    def test_create_new_version(self):
        collection = CollectionFactory()
        file = FileFactory()
        collection.files.add(file)

        collection_v = collection.create_new_version()

        assert file.versions.count() == 1

        assert collection_v.files.first() == file.versions.first()
