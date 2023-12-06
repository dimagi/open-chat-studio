import json
from datetime import datetime, timedelta

import pytest
import pytz
from django.urls import reverse
from django_celery_beat.models import ClockedSchedule, PeriodicTask

from apps.experiments.models import Experiment, ScheduledMessage
from apps.experiments.views.experiment import _source_material_is_missing
from apps.utils.factories import experiment as experiment_factory


def test_create_experiment_success(db, client):
    source_material = experiment_factory.SourceMaterialFactory()
    user = source_material.owner
    team = source_material.team
    consent_form = experiment_factory.ConsentFormFactory(team=team)
    prompt = experiment_factory.PromptFactory(team=team)
    client.force_login(user)

    post_data = {
        "name": "some name",
        "description": "Some description",
        "chatbot_prompt": prompt.id,
        "source_material": source_material.id if source_material else "",
        "consent_form": consent_form.id,
        "temperature": 0.7,
        "llm": "gpt-3.5",
    }

    client.post(reverse("experiments:new", args=[team.slug]), data=post_data)
    experiment = Experiment.objects.filter(owner=user).first()
    assert experiment is not None


@pytest.mark.parametrize(
    "create_source_material,promp_str",
    [
        (True, "You're an assistant"),
        (True, "Answer questions from this source: {source_material}"),
        (False, "You're an assistant"),
    ],
)
def test_experiment_does_not_require_source_material(db, create_source_material, promp_str):
    """Tests the `_source_material_is_missing` method"""
    material = None
    if create_source_material:
        material = experiment_factory.SourceMaterialFactory()
    experiment = experiment_factory.ExperimentFactory(chatbot_prompt__prompt=promp_str, source_material=material)
    assert _source_material_is_missing(experiment) is False


@pytest.mark.parametrize(
    "source_material,promp_str",
    [
        (None, "Answer questions from this source: {source_material}"),
    ],
)
def test_source_material_is_missing(db, source_material, promp_str):
    experiment = experiment_factory.ExperimentFactory(chatbot_prompt__prompt=promp_str, source_material=source_material)
    assert _source_material_is_missing(experiment) is True


def _get_value_from_periodic_task_kwargs(periodic_task: PeriodicTask, key: str):
    return json.loads(periodic_task.kwargs)[key]


def test_create_scheduled_message(db):
    """Creating a ScheduledMessage should also create a PeriodicTask and ClockedSchedule"""
    experiment = experiment_factory.ExperimentFactory()
    clocked = datetime.utcnow() + timedelta(hours=1)
    scheduled_message = ScheduledMessage.objects.create(
        experiment=experiment,
        owner=experiment.owner,
        team=experiment.team,
        name="Test1",
        clocked_schedule=clocked,
        message="Hello human",
        chat_ids=["1", "2"],
    )

    periodic_task = PeriodicTask.objects.get(name=f"{scheduled_message.team.slug}-{scheduled_message.name}")
    assert scheduled_message.periodic_task == periodic_task
    assert scheduled_message.clocked_schedule is not None


def test_update_scheduled_message(db):
    """Updating a ScheduledMessage should also update its underlying PeriodicTask and ClockedSchedule"""
    UTC = pytz.timezone("UTC")
    experiment = experiment_factory.ExperimentFactory()
    clocked = (datetime.now() + timedelta(hours=1)).astimezone(UTC)
    scheduled_message = ScheduledMessage.objects.create(
        experiment=experiment,
        owner=experiment.owner,
        team=experiment.team,
        name="Test1",
        clocked_schedule=clocked,
        message="Hello human",
        chat_ids=["1", "2"],
    )

    # Let's assert some values ass a baseline
    periodic_task = scheduled_message.periodic_task
    assert _get_value_from_periodic_task_kwargs(periodic_task, "chat_ids") == ["1", "2"]
    prev_clocked_time = periodic_task.clocked.clocked_time
    scheduled_message.chat_ids = ["3"]

    # Now update the scheduled message
    seconds_diff = 159
    scheduled_message.clocked_schedule = prev_clocked_time + timedelta(seconds=seconds_diff)
    scheduled_message.save()
    periodic_task.refresh_from_db()

    # Let's check again
    assert _get_value_from_periodic_task_kwargs(periodic_task, "chat_ids") == ["3"]
    new_clocked_time = periodic_task.clocked.clocked_time
    time_diff = new_clocked_time - prev_clocked_time
    time_diff.seconds == seconds_diff


def test_delete_scheduled_message(db):
    """Deleting a ScheduledMessage should also delete its PeriodicTask and ClockedSchedule"""
    experiment = experiment_factory.ExperimentFactory()
    clocked = datetime.utcnow() + timedelta(hours=1)
    scheduled_message = ScheduledMessage.objects.create(
        experiment=experiment,
        owner=experiment.owner,
        team=experiment.team,
        name="Test1",
        clocked_schedule=clocked,
        message="Hello human",
        chat_ids=["1", "2"],
    )
    periodic_task = scheduled_message.periodic_task
    clocked = periodic_task.clocked

    assert PeriodicTask.objects.filter(id=periodic_task.id).exists()
    assert ClockedSchedule.objects.filter(id=clocked.id).exists()
    scheduled_message.delete()
    assert PeriodicTask.objects.filter(id=periodic_task.id).exists() is False
    assert ClockedSchedule.objects.filter(id=clocked.id).exists() is False
