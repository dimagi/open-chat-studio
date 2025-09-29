from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Lower


def reconcile_connect_participants(ParticipantModel):
    """
    Reconcile connect channel participants with the same identifier but different casing.

    The commcare_connect channel expects lowercase participant identifiers, but some participants
    have been created with uppercase identifiers.

    This function identifies participants on the "commcare_connect" platform whose identifiers differ only by case
    (e.g., "ABC123" vs "abc123"). It merges sessions from the uppercase identifier participant into the lowercase
    identifier participant and then deletes the uppercase participant. Finally, it ensures that all remaining
    participants have lowercase identifiers.
    """
    lower_case_id_query = ParticipantModel.objects.filter(
        identifier=OuterRef("id_lower"), team=OuterRef("team_id")
    ).values("id")[:1]
    queryset = (
        ParticipantModel.objects.filter(platform="commcare_connect")
        .annotate(
            id_lower=Lower("identifier"),
        )
        .annotate(lc_participant_id=Subquery(lower_case_id_query))
        .exclude(
            # Ensure we're looking at uppercase identifiers only
            identifier=F("id_lower")
        )
        .distinct()
    )

    # Find participants that have a lowercased duplicate
    duplicates_queryset = queryset.filter(lc_participant_id__isnull=False)

    # For each duplicate, move their sessions to the lowercased participant and delete the duplicate
    for upper_part in duplicates_queryset.iterator(chunk_size=500):
        # Move existing sessions to the lowercased participant
        upper_part.experimentsession_set.update(participant_id=upper_part.lc_participant_id)
        # This will delete the participant data as well
        upper_part.delete()

    # Update any remaining participants with uppercase identifiers to have lowercased identifiers
    queryset.filter(lc_participant_id__isnull=True).update(identifier=F("id_lower"))
