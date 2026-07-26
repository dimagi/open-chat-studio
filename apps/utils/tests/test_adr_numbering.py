"""Guard tests for the ADR knowledge base.

Two concerns:

* **Numbering** — each ADR file is uniquely numbered and its heading matches the filename.
* **Freshness** — the ADR set stays consistent across the three surfaces that must agree
  (the files in ``docs/adr/``, the index table in ``docs/adr/index.md``, and the
  ``Decisions`` navigation in ``mkdocs.yml``), and relative links between ADRs resolve.

The ``/extract-adrs`` skill maintains the index and nav by hand; this makes drift a CI
failure, so a new ADR can't merge without being wired into navigation and a renamed or
removed one can't leave dangling references.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ADR_DIR = REPO_ROOT / "docs" / "adr"
MKDOCS = REPO_ROOT / "mkdocs.yml"

ADR_FILENAME_RE = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")
ADR_HEADING_RE = re.compile(r"^# ADR-(\d{4}):")
# Table rows in index.md: | [0001](0001-title.md) | ...status... | ...title... |
INDEX_ROW_RE = re.compile(r"^\|\s*\[(\d{4})\]\((\d{4}-[a-z0-9-]+\.md)\)\s*\|")
# Nav entries in mkdocs.yml: "- 0001 Title: adr/0001-title.md"
MKDOCS_ADR_RE = re.compile(r"adr/(\d{4}-[a-z0-9-]+\.md)")
# Relative markdown links inside an ADR body: [text](target.md) or (target.md#anchor)
MD_LINK_RE = re.compile(r"\]\((?!https?://)([^)]+?\.md)(?:#[^)]*)?\)")


def _adr_files() -> list[Path]:
    return sorted(p for p in ADR_DIR.iterdir() if ADR_FILENAME_RE.match(p.name))


def _adr_filenames() -> set[str]:
    return {p.name for p in _adr_files()}


def _index_referenced_filenames() -> set[str]:
    referenced = set()
    for line in (ADR_DIR / "index.md").read_text(encoding="utf-8").splitlines():
        match = INDEX_ROW_RE.match(line)
        if match:
            referenced.add(match.group(2))
    return referenced


def _mkdocs_referenced_filenames() -> set[str]:
    return set(MKDOCS_ADR_RE.findall(MKDOCS.read_text(encoding="utf-8")))


def test_adr_directory_populated():
    assert ADR_DIR.is_dir(), f"expected ADR directory at {ADR_DIR}"
    assert _adr_files(), f"no ADR files found in {ADR_DIR}"


def test_adr_numbers_unique():
    numbers = [ADR_FILENAME_RE.match(p.name).group(1) for p in _adr_files()]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not duplicates, f"duplicate ADR numbers detected: {duplicates}"


def test_adr_heading_matches_filename():
    mismatches = []
    for path in _adr_files():
        file_number = ADR_FILENAME_RE.match(path.name).group(1)
        first_line = path.read_text(encoding="utf-8").splitlines()[0] if path.stat().st_size else ""
        heading_match = ADR_HEADING_RE.match(first_line)
        if not heading_match:
            mismatches.append(f"{path.name}: first line is not '# ADR-NNNN: ...' (got {first_line!r})")
            continue
        if heading_match.group(1) != file_number:
            mismatches.append(
                f"{path.name}: filename number {file_number} does not match heading number {heading_match.group(1)}"
            )
    assert not mismatches, "\n".join(mismatches)


def test_every_adr_has_an_index_row():
    files = _adr_filenames()
    referenced = _index_referenced_filenames()
    missing = sorted(files - referenced)
    stale = sorted(referenced - files)
    assert not missing, f"ADR files with no row in docs/adr/index.md: {missing}"
    assert not stale, f"docs/adr/index.md rows pointing at non-existent ADR files: {stale}"


def test_every_adr_has_a_mkdocs_nav_entry():
    files = _adr_filenames()
    referenced = _mkdocs_referenced_filenames()
    missing = sorted(files - referenced)
    stale = sorted(referenced - files)
    assert not missing, f"ADR files with no nav entry under 'Decisions' in mkdocs.yml: {missing}"
    assert not stale, f"mkdocs.yml nav entries pointing at non-existent ADR files: {stale}"


def test_index_row_number_matches_filename():
    mismatches = []
    for line in (ADR_DIR / "index.md").read_text(encoding="utf-8").splitlines():
        match = INDEX_ROW_RE.match(line)
        if match and match.group(1) != match.group(2)[:4]:
            mismatches.append(line.strip())
    assert not mismatches, "index.md rows where the [NNNN] label disagrees with the filename:\n" + "\n".join(mismatches)


def test_relative_links_between_adrs_resolve():
    broken = []
    for path in sorted(p for p in ADR_DIR.iterdir() if p.suffix == ".md"):
        for target in MD_LINK_RE.findall(path.read_text(encoding="utf-8")):
            if "NNNN" in target:  # documented placeholder in _template.md and the index row-format hint
                continue
            if not (path.parent / target).resolve().is_file():
                broken.append(f"{path.name} -> {target}")
    assert not broken, "ADR documents contain relative links to files that do not exist:\n" + "\n".join(broken)
