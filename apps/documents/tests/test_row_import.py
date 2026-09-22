import pydantic
import pytest

from apps.documents.datamodels import CollectionFileMetadata, RowImportSettings
from apps.utils.factories.documents import CollectionFileFactory


class TestRowImportSettings:
    def test_metadata_columns_are_capped_at_sixteen(self):
        RowImportSettings(metadata_columns=[f"c{i}" for i in range(16)])
        with pytest.raises(pydantic.ValidationError):
            RowImportSettings(metadata_columns=[f"c{i}" for i in range(17)])

    def test_collection_file_metadata_without_a_chunking_strategy(self):
        metadata = CollectionFileMetadata(row_import=RowImportSettings(metadata_columns=["language"]))
        assert metadata.chunking_strategy is None
        assert metadata.row_import.metadata_columns == ["language"]

    def test_existing_metadata_still_loads(self):
        metadata = CollectionFileMetadata.model_validate(
            {"chunking_strategy": {"chunk_size": 800, "chunk_overlap": 400}}
        )
        assert metadata.row_import is None
        assert metadata.chunking_strategy.chunk_size == 800


@pytest.mark.django_db()
class TestCollectionFileRowImport:
    def test_row_import_is_none_for_a_text_file(self):
        collection_file = CollectionFileFactory.create()
        assert collection_file.row_import is None
        assert collection_file.chunking_strategy.chunk_size == 400

    def test_row_import_returns_the_settings(self):
        collection_file = CollectionFileFactory.create(metadata={"row_import": {"metadata_columns": ["language"]}})
        collection_file.refresh_from_db()
        assert collection_file.row_import.metadata_columns == ["language"]
        assert collection_file.chunking_strategy is None
