import django.contrib.postgres.indexes
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    """Build the GIN index backing lexical search.

    Operator notes:

    * ``CREATE INDEX CONCURRENTLY`` does not take a write lock, so indexing and retrieval keep
      working while it builds. It cannot run inside a transaction, hence ``atomic = False``.
    * A concurrent build that fails leaves an INVALID index behind. Drop it
      (``DROP INDEX CONCURRENTLY file_chunk_search_vector_idx``) before re-running, otherwise the
      retry fails on the existing name. Splitting this from the column add above is what makes
      that retry possible at all.
    """

    atomic = False

    dependencies = [
        ("files", "0015_filechunkembedding_search_vector"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="filechunkembedding",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="file_chunk_search_vector_idx"
            ),
        ),
    ]
