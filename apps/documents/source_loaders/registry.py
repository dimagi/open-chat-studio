"""Registry for document source loaders"""

from apps.documents.models import SourceType
from apps.documents.source_loaders.base import BaseDocumentLoader
from apps.documents.source_loaders.github import GitHubDocumentLoader


class LoaderRegistry:
    """Registry for managing document loaders"""

    _loaders: dict[str, type[BaseDocumentLoader]] = {}

    @classmethod
    def register(cls, source_type: str, loader_class: type[BaseDocumentLoader]):
        """Register a loader for a source type"""
        cls._loaders[source_type] = loader_class

    @classmethod
    def get_loader_class(cls, source_type: str) -> type[BaseDocumentLoader]:
        """Get loader class for a source type"""
        if source_type not in cls._loaders:
            raise ValueError(f"No loader registered for source type: {source_type}")
        return cls._loaders[source_type]

    @classmethod
    def get_available_types(cls) -> list[str]:
        """Get list of available source types"""
        return list(cls._loaders.keys())


# Register built-in loaders
LoaderRegistry.register(SourceType.GITHUB, GitHubDocumentLoader)


def create_loader(source_type: str, config: dict, collection) -> BaseDocumentLoader:
    """
    Factory function to create a document loader.

    Args:
        source_type: Type of source (github, confluence, etc.)
        config: Configuration for the loader
        collection: Collection instance

    Returns:
        Configured document loader instance
    """
    loader_class = LoaderRegistry.get_loader_class(source_type)

    # Convert config dict to appropriate config object based on source type
    if source_type == SourceType.GITHUB:
        from apps.documents.models import GitHubSourceConfig

        config_obj = GitHubSourceConfig(**config)
        return loader_class(config_obj, collection)
    elif source_type == SourceType.CONFLUENCE:
        from apps.documents.models import ConfluenceSourceConfig

        config_obj = ConfluenceSourceConfig(**config)
        return loader_class(config_obj, collection)
    else:
        raise ValueError(f"Unknown source type: {source_type}")
