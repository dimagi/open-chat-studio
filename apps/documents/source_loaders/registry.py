from apps.documents.models import SourceType
from apps.documents.source_loaders.base import BaseDocumentLoader
from apps.documents.source_loaders.confluence import ConfluenceDocumentLoader
from apps.documents.source_loaders.github import GitHubDocumentLoader

LOADERS = {SourceType.GITHUB: GitHubDocumentLoader, SourceType.CONFLUENCE: ConfluenceDocumentLoader}


def create_loader(collection, document_source) -> BaseDocumentLoader:
    """
    Factory function to create a document loader.

    Args:
        collection: Collection instance
        document_source: DocumentSource instance

    Returns:
        Configured document loader instance
    """
    source_type = document_source.source_type
    try:
        loader_class = LOADERS[source_type]
    except KeyError:
        available_types = list(LOADERS.keys())
        raise ValueError(
            f"No loader registered for source type: {source_type}. Available types: {available_types}"
        ) from None
    return loader_class.for_document_source(collection, document_source)
