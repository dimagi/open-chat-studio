import pydantic
import pytest

from apps.documents.datamodels import CollectionFileMetadata, RowImportSettings
from apps.documents.models import FAILURE_REASON_MAX_LENGTH
from apps.documents.row_import import (
    ParsedRow,
    ParsedSheet,
    RowFailure,
    RowImportError,
    content_hash,
    count_tokens,
    format_row_failures,
    oversized_row_numbers,
    parse_sheet,
    render_row,
    row_metadata,
)
from apps.utils.factories.documents import CollectionFileFactory

FAQ = (
    b"question,answer,language\n"
    b"When is the clinic open?,08:00 to 16:00,en\n"
    b"Ivulwa nini ikliniki?,08:00 ukuya ku-16:00,xh\n"
)


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


class TestParseSheet:
    def test_reads_headers_and_rows(self):
        sheet = parse_sheet(FAQ, filename="faq.csv")

        assert sheet.headers == ["question", "answer", "language"]
        assert sheet.rows[0] == ParsedRow(
            row_number=1, values={"question": "When is the clinic open?", "answer": "08:00 to 16:00", "language": "en"}
        )
        assert sheet.rows[1].row_number == 2
        assert sheet.rows[1].values["language"] == "xh"

    def test_strips_a_utf8_bom(self):
        sheet = parse_sheet(b"\xef\xbb\xbf" + FAQ, filename="faq.csv")
        assert sheet.headers[0] == "question"

    def test_tsv_uses_a_tab_delimiter(self):
        sheet = parse_sheet(b"a\tb\n1,2\t3\n", filename="faq.tsv")
        assert sheet.rows[0].values == {"a": "1,2", "b": "3"}

    def test_sniffs_a_semicolon_delimiter(self):
        sheet = parse_sheet(b"a;b\n1;2\n", filename="faq.csv")
        assert sheet.rows[0].values == {"a": "1", "b": "2"}

    def test_falls_back_to_detected_encoding(self):
        data = "a,b\ncafé,niño\n".encode("latin-1")
        sheet = parse_sheet(data, filename="faq.csv")
        assert sheet.rows[0].values == {"a": "café", "b": "niño"}

    def test_skips_fully_blank_lines(self):
        sheet = parse_sheet(b"a,b\n1,2\n\n3,4\n", filename="faq.csv")
        assert [row.row_number for row in sheet.rows] == [1, 2]

    def test_empty_cell_is_stored_as_empty_string(self):
        sheet = parse_sheet(b"a,b\n1,\n", filename="faq.csv")
        assert sheet.rows[0].values == {"a": "1", "b": ""}

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            pytest.param(b"", "no header row", id="empty-file"),
            pytest.param(b"a,,c\n1,2,3\n", "Column 2 has no name", id="blank-header"),
            pytest.param(b"a,b,a\n1,2,3\n", "Duplicate column name: a", id="duplicate-header"),
            pytest.param(b"a,b\n1,2,3\n", "Row 1 has 3 values but the header has 2", id="ragged-row"),
            pytest.param(b"a,b\n", "no data rows", id="header-only"),
        ],
    )
    def test_rejects_malformed_sheets(self, data, message):
        with pytest.raises(RowImportError, match=message):
            parse_sheet(data, filename="faq.csv")

    def test_rejects_more_rows_than_the_cap(self):
        data = b"a\n" + b"1\n" * 3
        with pytest.raises(RowImportError, match="more than 2 rows"):
            parse_sheet(data, filename="faq.csv", max_rows=2)

    def test_rejects_unsupported_extension(self):
        with pytest.raises(RowImportError, match="csv or tsv"):
            parse_sheet(FAQ, filename="faq.xlsx")


class TestRenderRow:
    def test_renders_filename_then_one_line_per_column(self):
        sheet = parse_sheet(FAQ, filename="faq.csv")
        text = render_row("faq.csv", sheet.headers, sheet.rows[0])
        assert text == "faq.csv\nquestion: When is the clinic open?\nanswer: 08:00 to 16:00\nlanguage: en"

    def test_strips_nul_bytes(self):
        row = ParsedRow(row_number=1, values={"a": "x\x00y"})
        assert render_row("f.csv", ["a"], row) == "f.csv\na: xy"


class TestRowMetadata:
    def test_picks_only_the_mapped_columns(self):
        row = ParsedRow(row_number=1, values={"question": "q", "language": "en", "district": ""})
        assert row_metadata(row, ["language", "district"]) == {"language": "en", "district": ""}


class TestContentHash:
    def test_is_stable_and_sensitive_to_metadata(self):
        first = content_hash("text", {"language": "en"})
        assert first == content_hash("text", {"language": "en"})
        assert len(first) == 64
        assert first != content_hash("text", {"language": "xh"})


class TestOversizedRows:
    def test_reports_rows_over_the_token_limit(self):
        sheet = ParsedSheet(
            headers=["a"],
            rows=[ParsedRow(row_number=1, values={"a": "short"}), ParsedRow(row_number=2, values={"a": "word " * 50})],
        )
        assert oversized_row_numbers(sheet, filename="f.csv", max_tokens=20) == [2]

    def test_count_tokens_counts_something(self):
        assert count_tokens("hello world") == 2


class TestFormatRowFailures:
    def test_empty_when_nothing_failed(self):
        assert format_row_failures([], total_rows=10) == ""

    def test_lists_row_numbers_and_reasons(self):
        text = format_row_failures(
            [RowFailure(4, "ValueError: too long"), RowFailure(9, "ValueError: rate limited")], total_rows=120
        )
        assert text == "2 of 120 rows failed to index. Row 4: ValueError: too long; row 9: ValueError: rate limited"

    def test_is_capped(self):
        failures = [RowFailure(i, "ValueError: " + "x" * 40) for i in range(1, 40)]
        assert len(format_row_failures(failures, total_rows=40)) == FAILURE_REASON_MAX_LENGTH
