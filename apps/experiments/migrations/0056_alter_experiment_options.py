# Generated by Django 4.2.7 on 2023-12-11 08:32

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0055_consentform_confirmation_text_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="experiment",
            options={
                "ordering": ["name"],
                "permissions": [
                    ("invite_participants", "Invite experiment participants"),
                    ("download_chats", "Download experiment chats"),
                ],
            },
        ),
    ]
