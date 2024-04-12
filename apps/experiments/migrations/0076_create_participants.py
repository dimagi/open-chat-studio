# Generated by Django 4.2.7 on 2024-04-10 09:21

from django.db import migrations, transaction


@transaction.atomic
def _create_participants(apps, schema_editor):
    Participant = apps.get_model("experiments", "Participant")
    ExperimentSession = apps.get_model("experiments", "ExperimentSession")

    # Let's first remove all orphan participants. These will cause issues during the next migration where we mark
    # external_chat_id as not-nullable. I'm not sure how they came to be that way, but assuming it was a bug,
    # which is why I prefer to remove them in a migration rather than manually removing them (to be friendly to
    # 3rd parties hosting OCS that might also have this issue)
    Participant.objects.filter(experimentsession__isnull=True).all().delete()

    for session in ExperimentSession.objects.all():
        if session.user:
            print(f"Session {session.id} has a user ({session.user.id})")
            # We're now working with system users. There might exist a Participant with identifier = user.email
            if session.participant:
                # The user acted as a participant, either using their own email or some other identifier. Either
                # way, we leave that alone
                print(f"Session {session.id} has a participant ({session.participant.id})")
                participant = session.participant
                if participant.external_chat_id is None:
                    participant.external_chat_id = participant.identifier
                if participant.user is None:
                    participant.user = session.user
                participant.save()
            else:
                # There might exist a participant for this user (with identifier = user email). We must link that
                # participant to this session and make sure its updated
                print(f"Session {session.id} does not have a participant")
                try:
                    participant = Participant.objects.get(
                        identifier=session.user.email,
                        team=session.team,
                    )
                    if participant.external_chat_id is None:
                        participant.external_chat_id = participant.identifier
                    if participant.user is None:
                        participant.user = session.user
                    participant.save()
                except Participant.DoesNotExist:
                    participant = Participant.objects.create(
                        identifier=session.user.email,
                        team=session.team,
                        external_chat_id=session.user.email,
                        user=session.user
                    )

                session.participant = participant
                session.save()
        else:
            print(f"Session {session.id} has no user")
            # We're now working with external users. There will be a single Participant with identfier = x per team
            # These sessions doesn't have a user, but may or may not have a participant (web chats have one,
            # external chats doesn't)
            participant = session.participant
            if participant:
                print(f"Session {session.id} has a participant ({participant.id})")
                if participant.external_chat_id is None:
                    print(f"Participant external_chat_id is None")
                    participant.external_chat_id = participant.identifier
                    participant.save()
                else:
                    print(f"Participant's external_chat_id is already set")
            else:
                # Since the identifier is unique per team, we need some value to put in there. Let's use the
                # external_chat_id
                print(f"Session {session.id} does not have a participant")
                participant, _ = Participant.objects.get_or_create(
                    team=session.team,
                    external_chat_id=session.external_chat_id,
                    identifier=session.external_chat_id
                )
                session.participant = participant
                session.save()


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0075_alter_participant_unique_together_and_more'),
    ]

    operations = [migrations.RunPython(_create_participants, migrations.RunPython.noop, elidable=True),]
