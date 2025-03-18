from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q, QuerySet


def similarity_search(
    queryset: QuerySet, search_phase: str, columns: list[str], score=0.2, extra_conditions: Q = None
) -> QuerySet:
    """
    Performs a similarity search on the queryset based on the search_phase and the columns provided. The result is
    ordered by the similarity score in descending order.

    :param queryset: The queryset to search on.
    :param search_phase: The search phase to search for.
    :param columns: The column names to search on.
    :param score: The score which at which the result is considered a match.
    :param extra_conditions: Extra Q conditions to filter on. `extra_conditions` will be OR-ed with the similarity
    score.
    """
    extra_conditions = extra_conditions or Q()

    total_similarity = None
    for column in columns:
        if not total_similarity:
            total_similarity = TrigramSimilarity(column, search_phase)
        else:
            total_similarity += TrigramSimilarity(column, search_phase)

    queryset = (
        queryset.annotate(similarity=total_similarity)
        .filter(Q(similarity__gt=score) | extra_conditions)
        .order_by("-similarity")
    )
    return queryset
