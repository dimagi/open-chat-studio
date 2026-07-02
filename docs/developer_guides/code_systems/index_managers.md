# Index Manager Classes

Index managers provide an abstraction layer for managing document embeddings and vector stores in Open Chat Studio. They are LLM provider-specific implementations that enable both remote (provider-hosted) and local (self-hosted) indexing strategies for document collections.

Index managers are the implementation layer behind [indexed collections](https://docs.openchatstudio.com/concepts/collections/indexed/), the user-facing feature that lets a chatbot search uploaded documents for relevant information before responding (retrieval-augmented generation).

See also: [DeepWiki: Document Collections and RAG](https://deepwiki.com/dimagi/open-chat-studio/7-document-collections-and-rag) for AI-generated Q&A-style exploration of this code.

## Architecture Overview

The system supports two indexing strategies:
- **Remote indexing**: Vector stores are created and managed by external providers (e.g., OpenAI)
- **Local indexing**: Embeddings are generated locally and stored in the application database

The index manager system follows an abstract base class pattern with two main hierarchies:

```
RemoteIndexManager (ABC)
├── OpenAIRemoteIndexManager

LocalIndexManager (ABC)
├── OpenAILocalIndexManager
├── GoogleLocalIndexManager
└── VoyageAILocalIndexManager
```


## Core Classes

### RemoteIndexManager

Abstract base class for managing vector stores in remote indexing services. Provides a common interface for interacting with external vector store providers.

### OpenAIRemoteIndexManager

OpenAI-specific implementation for managing file uploads and vector stores using [OpenAI's vector store API](https://developers.openai.com/api/reference/resources/vector_stores).

### LocalIndexManager

Abstract base class for managing local embedding operations. Handles text processing and embedding generation on the application side.

## Usage Example

### Getting an Index Manager

Index managers are obtained through the Collection model's `get_index_manager()` method:

```python
from apps.documents.models import Collection

# Get collection
collection = Collection.objects.get(id=collection_id)

# Get appropriate index manager based on collection configuration
index_manager = collection.get_index_manager()
```

The method returns:
- `RemoteIndexManager` instance if `collection.is_remote_index` is `True`
- `LocalIndexManager` instance if `collection.is_remote_index` is `False`

### Remote Index Operations

```python
# Create a new vector store
collection.ensure_remote_index_created(
    file_ids=["file-123", "file-456"]  # Optional initial files.
)

# Upload file to remote service
file = File.objects.get(id=file_id)
index_manager.upload_file_to_remote(file)

# Link files to existing vector store with chunking
index_manager.link_files_to_remote_index(
    file_ids=["file-789", "file-101"],
    chunk_size=1000,
    chunk_overlap=200
)

# Check if file exists remotely
exists = index_manager.file_exists_at_remote(file)

# Clean up - delete vector store
index_manager.delete_remote_index()
```

### Local Index Operations

`get_embedding_vector` requires an `input_type` of `"document"` or `"query"`. For Voyage and Google embedding providers, this routes to different underlying calls (`embed_documents` vs `embed_query`), which return different vectors — labelling each side correctly is what makes retrieval work as well as the provider can deliver. OpenAI's API currently treats `embed_documents` and `embed_query` identically (its embeddings have no input-type concept), so the choice is behaviourally a no-op there today; the routing is still applied in case that ever changes upstream or behind an OpenAI-compatible proxy.

```python
# Embed a retrieval query (matches the path used by Collection.get_query_vector)
query = "What does the user want to know?"
query_vector = index_manager.get_embedding_vector(query, input_type="query")

# Embed each document chunk during indexing
file = File.objects.get(id=file_id)
chunks = index_manager.chunk_file(file, chunk_size=500, chunk_overlap=50)
for chunk in chunks:
    embedding = index_manager.get_embedding_vector(chunk, input_type="document")
    # Store in FileChunkEmbedding model...
```

### Collection Integration

Index managers are typically used through the Collection model's indexing methods:

```python
from apps.documents.models import Collection, CollectionFile

collection = Collection.objects.get(id=collection_id)

# Add files to index (automatically chooses remote vs local)
collection_files = CollectionFile.objects.filter(
    collection=collection,
    status=FileStatus.PENDING
).iterator(100)

collection.add_files_to_index(
    collection_files=collection_files,
    chunk_size=1000,
    chunk_overlap=200
)
```

### Configuration-Driven Selection

The system automatically selects the appropriate manager based on collection settings:

```python
def get_index_manager(self):
    if self.is_index and self.is_remote_index:
        return self.llm_provider.get_remote_index_manager(
            self.openai_vector_store_id
        )
    else:
        return self.llm_provider.get_local_index_manager(
            embedding_model_name=self.embedding_provider_model.name
        )
```

## Citations

Once content is retrieved through an index manager, the LLM cites the source files it used and the platform renders those citations as a reference section. See [Citations](citations.md) for how that works.
