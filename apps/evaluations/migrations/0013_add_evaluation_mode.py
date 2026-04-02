from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('evaluations', '0012_backfill_evaluation_message_session_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='evaluationdataset',
            name='evaluation_mode',
            field=models.CharField(
                choices=[('message', 'Message'), ('session', 'Session')],
                default='message',
                help_text='Message mode stores individual message pairs; Session mode stores entire conversations',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='evaluator',
            name='evaluation_mode',
            field=models.CharField(
                choices=[('message', 'Message'), ('session', 'Session')],
                default='message',
                help_text='Message mode evaluates individual message pairs; Session mode evaluates entire conversations',
                max_length=10,
            ),
        ),
    ]
