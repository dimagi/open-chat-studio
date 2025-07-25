from apps.documents.models import SourceType
from apps.documents.source_loaders.base import BaseDocumentLoader
from apps.documents.source_loaders.github import GitHubDocumentLoader


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
    loaders = {SourceType.GITHUB: GitHubDocumentLoader}
    try:
        loader_class = loaders[source_type]
    except KeyError:
        raise ValueError(f"No loader registered for source type: {source_type}") from None
    return loader_class.for_document_source(collection, document_source)
