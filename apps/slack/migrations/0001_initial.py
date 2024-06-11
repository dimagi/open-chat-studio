# Generated by Django 4.2.11 on 2024-06-07 13:15

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("teams", "0005_invitation_groups"),
    ]

    operations = [
        migrations.CreateModel(
            name="SlackOAuthState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("state", models.CharField(max_length=64)),
                ("expire_at", models.DateTimeField()),
                ("config", models.JSONField(default=dict)),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="teams.team",
                        verbose_name="Team",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SlackBot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client_id", models.CharField(max_length=32)),
                ("app_id", models.CharField(max_length=32)),
                ("enterprise_id", models.CharField(max_length=32, null=True)),
                ("enterprise_name", models.TextField(null=True)),
                ("slack_team_id", models.CharField(max_length=32, null=True)),
                ("slack_team_name", models.TextField(null=True)),
                ("bot_token", models.TextField(null=True)),
                ("bot_refresh_token", models.TextField(null=True)),
                ("bot_token_expires_at", models.DateTimeField(null=True)),
                ("bot_id", models.CharField(max_length=32, null=True)),
                ("bot_user_id", models.CharField(max_length=32, null=True)),
                ("bot_scopes", models.TextField(null=True)),
                ("is_enterprise_install", models.BooleanField(null=True)),
                ("installed_at", models.DateTimeField()),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="teams.team",
                        verbose_name="Team",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=[
                            "client_id",
                            "enterprise_id",
                            "team_id",
                            "installed_at",
                        ],
                        name="slack_slack_client__f1774a_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="SlackInstallation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client_id", models.CharField(max_length=32)),
                ("app_id", models.CharField(max_length=32)),
                ("enterprise_id", models.CharField(max_length=32, null=True)),
                ("enterprise_name", models.TextField(null=True)),
                ("enterprise_url", models.TextField(null=True)),
                ("slack_team_id", models.CharField(max_length=32, null=True)),
                ("slack_team_name", models.TextField(null=True)),
                ("bot_token", models.TextField(null=True)),
                ("bot_refresh_token", models.TextField(null=True)),
                ("bot_token_expires_at", models.DateTimeField(null=True)),
                ("bot_id", models.CharField(max_length=32, null=True)),
                ("bot_user_id", models.TextField(null=True)),
                ("bot_scopes", models.TextField(null=True)),
                ("user_id", models.CharField(max_length=32)),
                ("user_token", models.TextField(null=True)),
                ("user_refresh_token", models.TextField(null=True)),
                ("user_token_expires_at", models.DateTimeField(null=True)),
                ("user_scopes", models.TextField(null=True)),
                ("incoming_webhook_url", models.TextField(null=True)),
                ("incoming_webhook_channel", models.TextField(null=True)),
                ("incoming_webhook_channel_id", models.TextField(null=True)),
                ("incoming_webhook_configuration_url", models.TextField(null=True)),
                ("is_enterprise_install", models.BooleanField(null=True)),
                ("token_type", models.CharField(max_length=32, null=True)),
                ("installed_at", models.DateTimeField()),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="teams.team",
                        verbose_name="Team",
                    ),
                ),
                ("import_all_channels", models.BooleanField(default=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=[
                            "client_id",
                            "enterprise_id",
                            "team_id",
                            "user_id",
                            "installed_at",
                        ],
                        name="slack_slack_client__644b24_idx",
                    )
                ],
            },
        ),
    ]