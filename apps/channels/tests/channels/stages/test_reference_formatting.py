import pytest

from apps.channels.channels_v2.stages.core import ResponseFormattingStage
from apps.channels.tests.channels.conftest import make_capabilities, make_context
from apps.utils.factories.files import FileFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def team():
    return TeamFactory.build()


class TestFormatReferenceSection:
    def setup_method(self):
        self.stage = ResponseFormattingStage()

    def _format(self, text, files, can_send_files=None):
        can_send_files = can_send_files or {}
        capabilities = make_capabilities(
            supports_files=True,
            can_send_file=lambda f: can_send_files.get(f.name, False),
        )
        ctx = make_context(capabilities=capabilities)
        return self.stage._format_reference_section(text, files, ctx)

    def test_converts_footnote_citations_to_regular_citations(self):
        """Test that [^1] style citations are converted to [1]"""
        text = "Here's a fact [^1] and another [^2]."
        formatted_text, uncited_files = self._format(text, files=[])

        assert "[^1]" not in formatted_text
        assert "[^2]" not in formatted_text
        assert "[1]" in formatted_text
        assert "[2]" in formatted_text

    def test_formats_reference_for_sendable_file(self, team):
        """Test that sendable files show only the filename"""
        file = FileFactory.build(id=1, name="report.pdf", team=team)

        text = """Here's a fact [^1].

[^1]: [report.pdf](http://example.com/report.pdf)"""

        formatted_text, uncited_files = self._format(text, files=[file], can_send_files={"report.pdf": True})

        assert "[1]: report.pdf" in formatted_text
        assert "http://example.com/report.pdf" not in formatted_text
        assert uncited_files == []

    def test_formats_reference_for_unsendable_file(self, team):
        """Test that unsendable files show filename with URL in parentheses"""
        file = FileFactory.build(id=1, name="report.txt", team=team)

        text = """Here's a fact [^1].

[^1]: [report.txt](http://example.com/report.txt)"""

        formatted_text, uncited_files = self._format(text, files=[file], can_send_files={"report.txt": False})

        assert "[1]: report.txt (http://example.com/report.txt)" in formatted_text
        assert uncited_files == []

    def test_mixed_sendable_and_unsendable_files(self, team):
        """Test handling of both sendable and unsendable files"""
        pdf_file = FileFactory.build(id=1, name="summary.pdf", team=team)
        txt_file = FileFactory.build(id=2, name="notes.txt", team=team)

        text = """Here's a fact [^1] and another [^2].

[^1]: [summary.pdf](http://example.com/summary.pdf)
[^2]: [notes.txt](http://example.com/notes.txt)"""

        formatted_text, uncited_files = self._format(
            text, files=[pdf_file, txt_file], can_send_files={"summary.pdf": True, "notes.txt": False}
        )

        assert "[1]: summary.pdf" in formatted_text
        assert "http://example.com/summary.pdf" not in formatted_text
        assert "[2]: notes.txt (http://example.com/notes.txt)" in formatted_text
        assert uncited_files == []

    def test_returns_uncited_files(self, team):
        """Test that files not referenced in text are returned as uncited"""
        file1 = FileFactory.build(id=1, name="cited.pdf", team=team)
        file2 = FileFactory.build(id=2, name="uncited.pdf", team=team)

        text = """Here's a fact [^1].

[^1]: [cited.pdf](http://example.com/cited.pdf)"""

        formatted_text, uncited_files = self._format(text, files=[file1, file2])

        assert len(uncited_files) == 1
        assert file2 in uncited_files
        assert file1 not in uncited_files

    def test_handles_no_files(self):
        """Test that function handles empty file list"""
        text = "Some text without references"
        formatted_text, uncited_files = self._format(text, files=[])

        assert formatted_text == text
        assert uncited_files == []

    def test_handles_reference_to_nonexistent_file(self, team):
        """Test that references to files not in the list are left unchanged"""
        file = FileFactory.build(id=1, name="existing.pdf", team=team)

        text = """Here's a fact [^1] and another [^2].

[^1]: [existing.pdf](http://example.com/existing.pdf)
[^2]: [nonexistent.pdf](http://example.com/nonexistent.pdf)"""

        formatted_text, uncited_files = self._format(text, files=[file])

        # The existing file should be formatted
        assert "[1]: existing.pdf" in formatted_text or "[1]: existing.pdf (" in formatted_text
        # The nonexistent file reference should remain unchanged
        assert "[2]: [nonexistent.pdf](http://example.com/nonexistent.pdf)" in formatted_text
        assert uncited_files == []

    def test_multiple_references_to_same_file(self, team):
        """Test handling multiple citations to the same file"""
        file = FileFactory.build(id=1, name="report.pdf", team=team)

        text = """First fact [^1] and second fact [^2].

[^1]: [report.pdf](http://example.com/report.pdf)
[^2]: [report.pdf](http://example.com/report.pdf)"""

        formatted_text, uncited_files = self._format(text, files=[file], can_send_files={"report.pdf": True})

        # Both references should be formatted
        assert "[1]: report.pdf" in formatted_text
        assert "[2]: report.pdf" in formatted_text
        assert uncited_files == []

    def test_all_files_uncited(self, team):
        """Test when none of the files are referenced in text"""
        file1 = FileFactory.build(id=1, name="file1.pdf", team=team)
        file2 = FileFactory.build(id=1, name="file2.pdf", team=team)

        text = "Some text without any references"

        formatted_text, uncited_files = self._format(text, files=[file1, file2])

        assert formatted_text == text
        assert len(uncited_files) == 2
        assert file1 in uncited_files
        assert file2 in uncited_files

    def test_preserves_text_without_citations(self):
        """Test that text without citations is preserved as-is"""
        text = """This is some text.

With multiple paragraphs.

And no citations at all."""

        formatted_text, uncited_files = self._format(text, files=[])

        assert formatted_text == text
        assert uncited_files == []

    def test_files_with_custom_citation_text(self, team):
        """Test that files with custom citation text get properly recognized"""
        file = FileFactory.build(id=1, name="report.pdf", team=team, metadata={"citation_text": "custom text"})

        text = """Here's a fact [^1].

[^1]: [custom text](http://example.com/report.pdf)"""

        formatted_text, uncited_files = self._format(text, files=[file], can_send_files={"report.pdf": True})

        assert "[1]: custom text" in formatted_text
        assert "http://example.com/report.pdf" not in formatted_text
        assert uncited_files == []
