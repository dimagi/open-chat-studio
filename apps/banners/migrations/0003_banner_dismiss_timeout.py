# Generated by Django 5.1.5 on 2025-07-04 10:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('banners', '0002_banner_feature_flag'),
    ]

    operations = [
        migrations.AddField(
            model_name='banner',
            name='dismiss_timeout',
            field=models.PositiveSmallIntegerField(default=0, help_text='The banner will re-appear this many days after being dismissed'),
        ),
    ]
