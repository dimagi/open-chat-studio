def _get_default_team(apps):
    Team = apps.get_model("teams", "Team")
    return Team.objects.order_by("id").first()


def assign_model_to_team_migration(model_name):
    def _migration_op(apps, schema_editor):
        model_cls = apps.get_model(model_name)
        default_team = _get_default_team(apps)
        if default_team:
            model_cls.objects.update(team=default_team)

    return _migration_op
