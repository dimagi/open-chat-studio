# Matches OCS citation markers in LLM output, e.g. ``<CIT 123 />``.
OCS_CITATION_PATTERN = r"<CIT\s+(?P<file_id>\d+)\s*/>"
