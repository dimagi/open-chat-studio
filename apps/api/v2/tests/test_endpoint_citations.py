"""Endpoints the v2 surface names in its own text are endpoints the schema serves.

Help text and descriptions point a client at other endpoints by their ``operationId`` rather than
by path, so a serializer can be mounted under a later API version without its prose going stale.
An id is only worth citing while something answers to it, and nothing else would catch a rename --
so a citation naming no operation fails here.
"""

import re

import pytest
import yaml

#: How this codebase cites an endpoint: its id in backticks, then the word "endpoint".
CITATION = re.compile(r"`([a-z][a-z0-9_]*)` endpoint")

#: Adjacent string literals, which is how a long description is written. Joined before scanning, so
#: that where a citation happens to fall across the wrap makes no difference to what is checked.
CONTINUATION = re.compile(r'"\s*\n\s*"')


@pytest.fixture()
def cited(pytestconfig) -> dict[str, list[str]]:
    """Every endpoint id the v2 source cites, by the file citing it."""
    v2 = pytestconfig.rootpath / "apps" / "api" / "v2"
    found: dict[str, list[str]] = {}
    for source in v2.rglob("*.py"):
        if "tests" in source.parts:
            continue
        if ids := CITATION.findall(CONTINUATION.sub("", source.read_text())):
            found[str(source.relative_to(pytestconfig.rootpath))] = ids
    return found


@pytest.fixture()
def served(pytestconfig) -> set[str]:
    """The operation ids the committed v2 schema serves."""
    with open(pytestconfig.rootpath / "api-schemas" / "v2.yml") as schema:
        paths = yaml.safe_load(schema)["paths"]
    return {
        operation["operationId"]
        for methods in paths.values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    }


def test_every_cited_endpoint_id_is_one_the_schema_serves(cited, served):
    unserved = {path: [name for name in ids if name not in served] for path, ids in cited.items()}
    assert not {path: ids for path, ids in unserved.items() if ids}


def test_the_citation_convention_is_the_one_the_source_uses(cited):
    """Guards the check above rather than the source: a reworded convention would leave the regex
    matching nothing, and every assertion about citations would then pass vacuously."""
    assert cited
