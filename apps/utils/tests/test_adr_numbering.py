import re
from pathlib import Path

ADR_DIR = Path(__file__).resolve().parents[3] / "docs" / "adr"
ADR_FILENAME_RE = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")
ADR_HEADING_RE = re.compile(r"^# ADR-(\d{4}):")


def _adr_files() -> list[Path]:
    return sorted(p for p in ADR_DIR.iterdir() if ADR_FILENAME_RE.match(p.name))


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
