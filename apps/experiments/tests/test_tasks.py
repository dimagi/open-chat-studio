from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.experiments.tasks import async_create_experiment_version, async_export_chat
from apps.files.models import File
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_async_export_chat_returns_file_id():
    session = ExperimentSessionFactory()
    result = async_export_chat.run(session.experiment_id, {}, "UTC")
    assert result == {"file_id": File.objects.first().id}


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_async_create_experiment_version():
    experiment = ExperimentFactory(create_version_task_id="asd123")
    async_create_experiment_version(experiment.id)
    assert experiment.versions.count() == 1
    experiment.refresh_from_db()
    assert experiment.create_version_task_id == ""


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@patch("apps.experiments.models.Experiment.create_new_version")
def test_async_create_experiment_version_fails(create_new_version):
    create_new_version.side_effect = Exception("Error")
    experiment = ExperimentFactory(create_version_task_id="asd123")
    with pytest.raises(Exception, match="Error"):
        async_create_experiment_version(experiment.id)
    assert experiment.versions.count() == 0
    experiment.refresh_from_db()
    assert experiment.create_version_task_id == ""
