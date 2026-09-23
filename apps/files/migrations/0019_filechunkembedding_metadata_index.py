import django.contrib.postgres.indexes
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    """Build the GIN index over row metadata.

    Operator notes:

    * ``CREATE INDEX CONCURRENTLY`` does not take a write lock, so indexing and retrieval keep
      working while it builds. It cannot run inside a transaction, hence ``atomic = False``.
    * A concurrent build that fails leaves an INVALID index behind. Drop it
      (``DROP INDEX CONCURRENTLY file_chunk_metadata_idx``) before re-running, otherwise the
      retry fails on the existing name.
    """

    atomic = False

    dependencies = [
        ("files", "0018_filechunkembedding_metadata_content_hash"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="filechunkembedding",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["metadata"], name="file_chunk_metadata_idx", opclasses=["jsonb_path_ops"]
            ),
        ),
    ]
