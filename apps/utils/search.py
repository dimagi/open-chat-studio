from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q, QuerySet


def similarity_search(
    queryset: QuerySet, search_phase: str, columns: list[str], score: float = 0.2, extra_conditions: Q | None = None
) -> QuerySet:
    """
    Performs a similarity search on the queryset using trigram similarity.

    Args:
        queryset: Base queryset to search on
        search_phase: Search term to match against
        columns: Database columns to search in
        score: The score above which to include results
        extra_conditions: Additional filter conditions

    Returns:
        QuerySet ordered by similarity score
    """
    conditions = extra_conditions or Q()

    # Calculate combined similarity across all columns
    similarity = sum(TrigramSimilarity(column, search_phase) for column in columns)

    return queryset.annotate(similarity=similarity).filter(Q(similarity__gt=score) | conditions).order_by("-similarity")
