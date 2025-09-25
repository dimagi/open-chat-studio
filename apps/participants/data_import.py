import csv
import io
import json

from django.db import transaction

from apps.experiments.models import Participant, ParticipantData


def process_participant_import(csv_file, experiment, team):
    """
    Process CSV file and import/update participant data.

    Expected CSV format:
    - identifier (required)
    - platform (required, must be valid ChannelPlatform choice)
    - name (optional)
    - data.* columns for custom participant data

    Returns dict with 'created', 'updated', 'errors' counts/lists
    """
    from apps.channels.models import ChannelPlatform

    # Read and decode file
    csv_file.seek(0)
    content = csv_file.read().decode("utf-8")
    csv_reader = csv.DictReader(io.StringIO(content))

    results = {"created": 0, "updated": 0, "errors": []}
    valid_platforms = [choice.value for choice in ChannelPlatform.for_dropdown([], team)]
    valid_platforms.append(ChannelPlatform.WEB.value)
    valid_platforms.sort()

    # Process each row
    for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 since row 1 is header
        try:
            # Validate required fields
            identifier = row.get("identifier", "").strip()
            platform = row.get("platform", "").strip()

            if not identifier:
                results["errors"].append(f"Row {row_num}: identifier is required")
                continue

            if not platform:
                results["errors"].append(f"Row {row_num}: platform is required")
                continue

            if platform not in valid_platforms:
                results["errors"].append(
                    f"Row {row_num}: invalid platform '{platform}'. Valid options: {', '.join(valid_platforms)}"
                )
                continue

            name = row.get("name", "").strip()

            # Extract participant data (columns starting with 'data.')
            participant_data = {}
            for key, value in row.items():
                if key.startswith("data.") and value:
                    data_key = key[5:]  # Remove 'data.' prefix
                    # Try to parse as JSON, fallback to string
                    try:
                        participant_data[data_key] = json.loads(value)
                    except json.JSONDecodeError:
                        participant_data[data_key] = value
            if participant_data and not experiment:
                results["errors"].append(f"Row {row_num}: participant data import requires a chatbot.")
            if name:
                participant_data |= {"name": name}

            # Create or update participant
            with transaction.atomic():
                participant, created = Participant.objects.get_or_create(
                    team=team, platform=platform, identifier=identifier, defaults={"name": name}
                )

                if not created and name:
                    # Update name if provided
                    participant.name = name
                    participant.save(update_fields=["name"])

                # Create or update participant data if any data.* columns exist
                if participant_data:
                    participant_data_obj, data_created = ParticipantData.objects.get_or_create(
                        participant=participant, experiment=experiment, team=team, defaults={"data": participant_data}
                    )

                    if not data_created:
                        # Update existing data by merging
                        participant_data_obj.data.update(participant_data)
                        participant_data_obj.save(update_fields=["data"])

                if created:
                    results["created"] += 1
                else:
                    results["updated"] += 1

        except Exception as e:
            results["errors"].append(f"Row {row_num}: {str(e)}")
            continue

    return results
