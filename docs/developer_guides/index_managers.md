# Index Manager Classes

Index managers provide an abstraction layer for managing document embeddings and vector stores in Open Chat Studio. They are LLM provider-specific implementations that enable both remote (provider-hosted) and local (self-hosted) indexing strategies for document collections.

## Architecture Overview

The index manager system follows an abstract base class pattern with two main hierarchies:

```
RemoteIndexManager (ABC)
├── OpenAIRemoteIndexManager

LocalIndexManager (ABC)
├── OpenAILocalIndexManager
```

The system supports two indexing strategies:
- **Remote indexing**: Vector stores are created and managed by external providers (e.g., OpenAI)
- **Local indexing**: Embeddings are generated locally and stored in the application database

## Core Classes

### RemoteIndexManager

Abstract base class for managing vector stores in remote indexing services. Provides a common interface for interacting with external vector store providers.

### OpenAIRemoteIndexManager

OpenAI-specific implementation for managing vector stores using OpenAI's vector store API.

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

```python
# Generate embedding for text content
content = "This is a sample document content."
embedding_vector = index_manager.get_embedding_vector(content)

# Chunk large text with overlap
text = "Long document content here..."
chunks = index_manager.chunk_content(
    text=text,
    chunk_size=500,
    chunk_overlap=50
)

# Process each chunk for storage
for i, chunk in enumerate(chunks):
    embedding = index_manager.get_embedding_vector(chunk)
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
