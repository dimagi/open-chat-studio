# Generated by Django 4.2 on 2023-10-12 12:55
from django.conf import settings
from django.db import migrations

from apps.experiments.const import DEFAULT_CONSENT_TEXT


def add_users_to_default_team(apps, schema_editor):
    Team = apps.get_model("teams", "Team")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Membership = apps.get_model("teams", "Membership")
    default_team = Team.objects.order_by("id").first()
    for user in User.objects.all():
        Membership.objects.get_or_create(team=default_team, user=user, defaults={
            "role": "member"
        })


def create_default_consent_form(apps, schema_editor):
    Team = apps.get_model("teams", "Team")
    ConsentForm = apps.get_model("experiments", "ConsentForm")
    for team in Team.objects.all():
        ConsentForm.objects.get_or_create(
            team=team,
            is_default=True,
            defaults={
                "name": "Default Consent",
                "consent_text": DEFAULT_CONSENT_TEXT,
            }
        )


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0040_make_team_not_null"),
    ]

    operations = [
        migrations.RunPython(create_default_consent_form, elidable=True),
        migrations.RunPython(add_users_to_default_team, elidable=True),
    ]
