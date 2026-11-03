import unicodedata
from typing import Any


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    text = "".join(run.get("textRun", {}).get("content", "") for run in paragraph.get("elements", []))
    if paragraph.get("paragraphStyle", {}).get("namedStyleType", "").startswith("HEADING_") and text.strip():
        text = "# " + text
    if paragraph.get("bullet") is not None and text.strip():
        text = "- " + text
    return text.rstrip("\n")


def _table_text(table: dict[str, Any]) -> list[str]:
    lines = []
    for row in table.get("tableRows", []):
        cells = [" ".join(_text_from_content(cell.get("content", []))).strip() for cell in row.get("tableCells", [])]
        lines.append(" | ".join(cells))
    return lines


def _append_section_break(lines: list[str]) -> None:
    if lines and lines[-1]:
        lines.append("")


def _text_from_content(content: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for element in content:
        if paragraph := element.get("paragraph"):
            lines.append(_paragraph_text(paragraph))
        elif table := element.get("table"):
            lines.extend(_table_text(table))
        elif toc := element.get("tableOfContents"):
            lines.extend(_text_from_content(toc.get("content", [])))
        elif element.get("sectionBreak"):
            _append_section_break(lines)
    return lines


def normalize_document_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    preserved = {"\n", "\t", "\u200c", "\u200d"}
    return "".join(
        char if char in preserved or unicodedata.category(char) not in {"Cc", "Cf"} else " " for char in text
    )


def _tab_title_lines(title: str | None, show_title: bool) -> list[str]:
    return [f"## {title}", ""] if title and show_title else []


def _append_tab_content(tab: dict[str, Any], lines: list[str], show_titles: bool, is_child: bool = False) -> None:
    properties = tab.get("tabProperties", {})
    lines.extend(_tab_title_lines(properties.get("title"), is_child or show_titles))
    body = tab.get("documentTab", {}).get("body", {})
    lines.extend(_text_from_content(body.get("content", [])))
    for child in tab.get("childTabs", []):
        _append_tab_content(child, lines, show_titles, is_child=True)


def extract_document_text(document: dict[str, Any]) -> str:
    lines: list[str] = []
    tabs = document.get("tabs") or []
    for tab in tabs:
        _append_tab_content(tab, lines, show_titles=len(tabs) > 1)
    if not tabs:
        lines.extend(_text_from_content(document.get("body", {}).get("content", [])))
    text = normalize_document_text("\n".join(lines)).strip()
    return f"{text}\n" if text else ""
