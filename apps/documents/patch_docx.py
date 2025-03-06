from docx.opc.oxml import parse_xml
from docx.opc.pkgreader import _SerializedRelationship, _SerializedRelationships


def patch_docx():
    """
    Workaround for python-docx issue with loading relationships from XML.
    See https://github.com/python-openxml/python-docx/issues/1351
    """

    def load_from_xml_v2(baseURI, rels_item_xml):
        srels = _SerializedRelationships()
        if rels_item_xml is not None:
            rels_elm = parse_xml(rels_item_xml)
            for rel_elm in rels_elm.Relationship_lst:
                if (
                    rel_elm.target_ref in ("../NULL", "NULL") or rel_elm.target_ref.startswith("#_")  # Styled headers
                ):
                    continue
                srels._srels.append(_SerializedRelationship(baseURI, rel_elm))
        return srels

    _SerializedRelationships.load_from_xml = load_from_xml_v2
