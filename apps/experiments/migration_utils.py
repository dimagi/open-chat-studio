from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Lower


def reconcile_connect_participants(ParticipantModel):
    """
    Reconcile connect channel participants with the same identifier but different casing.

    The commcare_connect channel expects lowercase participant identifiers, but some participants
    have been created with uppercase identifiers.

    This function identifies participants using the "commcare_connect" channel whose identifiers differ only by case
    (e.g., "ABC123" vs "abc123"). It merges sessions and scheduled messages from the uppercase identifier participant
    into the lowercase identifier participant and then deletes the uppercase participant. Finally, it ensures that all
    remaining participants have lowercase identifiers.
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
        lc_participant = ParticipantModel.objects.get(id=upper_part.lc_participant_id)

        # Move existing sessions to the lowercased participant
        upper_part.experimentsession_set.update(participant=lc_participant)
        # scheduled_messages is misspelled in the model's related name, so we have to use that name here
        upper_part.schduled_messages.update(participant=lc_participant)

        # Transfer any data sets that doesn't exist in the lowercased participant from the uppercased participant
        # This should cover the case where the lower cased participant did not chat to a specific chatbot, but the upper
        # cased participant did, in which case there will be sessions that are being transferred that have no data set
        # for that chatbot. We want to keep that data, since it contains encryption keys etc, so we transfer it over.
        # If a dataset for a specific chatbot already exists on the lowercased participant, we don't want to transfer
        # it over, since it will already contain the correct encryption keys etc.
        existing_chatbot_ids = lc_participant.data_set.values_list("experiment_id", flat=True).distinct()
        upper_part.data_set.exclude(experiment_id__in=existing_chatbot_ids).update(participant=lc_participant)

        # Transfer system metadata from upper_part to lc_participant where system_metadata is empty
        # Get system metadata from upper_part for chatbots that exist in both participants
        upper_metadata = {
            ds.experiment_id: ds.system_metadata for ds in upper_part.data_set.exclude(system_metadata={}).all()
        }

        # Update lc_participant data sets that have empty system_metadata with upper_part's metadata
        for data_set in lc_participant.data_set.filter(system_metadata={}).all():
            if data_set.experiment_id in upper_metadata:
                data_set.system_metadata = upper_metadata[data_set.experiment_id]
                data_set.save()

        # This will delete the participant data as well
        upper_part.delete()

    # Update any remaining participants with uppercase identifiers to have lowercased identifiers
    queryset.filter(lc_participant_id__isnull=True).update(identifier=F("id_lower"))
