from collections.abc import Iterator
from typing import Self

from apps.documents.datamodels import GoogleDocsSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument
from apps.documents.source_loaders.google_docs_api import fetch_google_document
from apps.documents.source_loaders.google_docs_content import build_source_document
from apps.documents.source_loaders.google_docs_urls import extract_document_id
from apps.service_providers.models import AuthProviderType


class GoogleDocsDocumentLoader(BaseDocumentLoader[GoogleDocsSourceConfig]):
    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        provider = document_source.auth_provider
        if not provider:
            raise ValueError("Google Docs document source requires an OAuth Authorization Code provider")
        if provider.team_id != document_source.team_id:
            raise ValueError("Google Docs authentication provider must belong to the same team")
        if provider.type != AuthProviderType.oauth_authorization_code:
            raise ValueError("Google Docs document source requires an OAuth Authorization Code provider")
        return cls(collection, document_source.config.google_docs, provider)

    def load_documents(self) -> Iterator[SourceDocument]:
        document_id = extract_document_id(self.config.document_url)
        document = fetch_google_document(self.auth_provider, document_id)
        yield build_source_document(document, document_id, self.collection.id)

    def get_document_identifier(self, document: SourceDocument) -> str:
        return document.metadata["document_id"]

    def should_update_document(self, document: SourceDocument, existing_file: CollectionFile) -> bool:
        existing_metadata = existing_file.file.metadata or {}
        content_changed = document.metadata.get("content_hash") != existing_metadata.get("content_hash")
        filename_changed = document.metadata.get("filename") != existing_file.file.name
        title_changed = document.metadata.get("title") != existing_metadata.get("title")
        return any((content_changed, filename_changed, title_changed))
