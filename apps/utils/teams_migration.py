def _get_default_team(apps):
    Team = apps.get_model("teams", "Team")
    return Team.objects.order_by("id").first()


def assign_model_to_team_migration(model_name, delete_if_no_team=False):
    def _migration_op(apps, schema_editor):
        model_cls = apps.get_model(model_name)
        default_team = _get_default_team(apps)
        if default_team:
            model_cls.objects.update(team=default_team)
        elif delete_if_no_team:
            model_cls.objects.all().delete()

    return _migration_op


def migrate_participants(apps, schema_editor):
    """Migrate participants to the default team, if they don't already exist there.
    If they do exist, migrate their sessions to the existing participant."""
    Participant = apps.get_model("participants.Participant")
    ExperimentSession = apps.get_model("experiments.ExperimentSession")
    default_team = _get_default_team(apps)
    if default_team and Participant.objects.all().exists():
        to_move = dict(Participant.objects.exclude(team=default_team).values_list("id", "email"))
        existing = dict(Participant.objects.filter(team=default_team).values_list("email", "id"))

        non_conflicts = []
        id_map = {}
        for p_id, email in to_move.items():
            if email in existing:
                id_map[p_id] = existing[email]
            else:
                non_conflicts.append(p_id)

        if non_conflicts:
            print("Updating team for participants with no conflicts:", non_conflicts)
            Participant.objects.filter(id__in=non_conflicts).update(team=default_team)

        for old_id, new_id in id_map.items():
            print("Migrating participant session:", old_id, new_id)
            ExperimentSession.objects.filter(participant_id=old_id).update(participant_id=new_id)
