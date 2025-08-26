import logging
from collections.abc import Iterator
from typing import Self

from langchain_community.document_loaders.confluence import ConfluenceLoader
from langchain_core.documents import Document

from apps.documents.datamodels import ConfluenceSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader
from apps.service_providers.models import AuthProviderType

logger = logging.getLogger(__name__)


class ConfluenceDocumentLoader(BaseDocumentLoader[ConfluenceSourceConfig]):
    """Document loader for Confluence spaces"""

    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        auth_provider = document_source.auth_provider
        if not auth_provider or auth_provider.type != AuthProviderType.basic:
            type_ = auth_provider.type if auth_provider else "None"
            raise ValueError(f"Confluence document source requires a basic authentication provider, got {type_}")
        if not auth_provider.config.get("username") or not auth_provider.config.get("password"):
            raise ValueError("Confluence authentication both username and password")
        return cls(collection, document_source.config.confluence, auth_provider)

    def load_documents(self) -> Iterator[Document]:
        """Load documents from Confluence space"""
        try:
            username = self.auth_provider.config.get("username")
            token = self.auth_provider.config.get("password")

            if not username:
                raise ValueError("Confluence authentication requires username or email")

            loader = ConfluenceLoader(
                url=self.config.base_url,
                username=username,
                api_key=token,
                space_key=self.config.space_key,
                include_attachments=False,  # Skip attachments for now
                max_pages=1000,
            )

            for document in loader.lazy_load():
                if not document.page_content.strip():
                    continue

                document.metadata.update(
                    {
                        "collection_id": self.collection.id,
                        "source_type": "confluence",
                        "base_url": self.config.base_url,
                        "space_key": self.config.space_key,
                    }
                )
                yield document

        except Exception as e:
            logger.error(f"Error loading documents from Confluence: {str(e)}")
            raise

    def get_document_identifier(self, document: Document) -> str:
        page_id = document.metadata.get("id")
        if page_id:
            return f"confluence://{self.config.space_key}/{page_id}"
        return document.metadata.get("source", "")

    def should_update_document(self, document: Document, existing_file: CollectionFile) -> bool:
        # Check if last modified time changed
        new_modified = document.metadata.get("when")
        old_modified = existing_file.file.metadata.get("when")

        if new_modified and old_modified:
            return new_modified != old_modified
        return super().should_update_document(document, existing_file)
