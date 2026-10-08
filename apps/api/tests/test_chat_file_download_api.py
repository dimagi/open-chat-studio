import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.session_tokens import issue_session_token
from apps.channels.models import ChannelPlatform
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.files import FileFactory

EMBED_KEY = "test_widget_token_123456789012"


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)


def _attach(session, **file_kwargs):
    file = FileFactory.create(team=session.team, **file_kwargs)
    session.chat.attachments.create(tool_type="ocs_attachments").files.add(file)
    return file


def _url(session, file):
    return reverse("api:chat:file-content", kwargs={"session_id": session.external_id, "file_id": file.id})


@pytest.mark.django_db()
def test_download_file_attached_to_session(api_client, session):
    file = _attach(session, name="report.csv", content_type="text/csv", file__data=b"a,b\n1,2\n")

    response = api_client.get(_url(session, file))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"a,b\n1,2\n"
    assert response["Content-Type"] == "text/csv"
    assert response["Content-Disposition"] == 'attachment; filename="report.csv"'


@pytest.mark.django_db()
def test_download_file_attached_to_multiple_attachments(api_client, session):
    file = _attach(session)
    session.chat.attachments.create(tool_type="code_interpreter").files.add(file)

    assert api_client.get(_url(session, file)).status_code == 200


def _other_session_file(session):
    other = ExperimentSessionFactory.create(experiment=session.experiment)
    return _attach(other)


def _other_team_file(session):
    return _attach(ExperimentSessionFactory.create())


def _unattached_file(session):
    return FileFactory.create(team=session.team)


def _file_without_storage(session):
    return _attach(session, file=None)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "make_file",
    [
        pytest.param(_other_session_file, id="other-session-same-team"),
        pytest.param(_other_team_file, id="other-team"),
        pytest.param(_unattached_file, id="not-attached-to-any-chat"),
        pytest.param(_file_without_storage, id="no-storage"),
    ],
)
def test_download_file_not_found(api_client, session, make_file):
    file = make_file(session)

    assert api_client.get(_url(session, file)).status_code == 404


@pytest.mark.django_db()
def test_download_requires_session_token(api_client, experiment):
    session = ExperimentSessionFactory.create(experiment=experiment)
    file = _attach(session)

    response = api_client.get(_url(session, file))
    assert response.status_code == 403
    assert response.json()["code"] == "session_token_required"

    response = api_client.get(_url(session, file), HTTP_X_SESSION_TOKEN=issue_session_token(session))
    assert response.status_code == 200


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("origin", "expected_status"),
    [
        pytest.param("https://example.com", 200, id="allowed-origin"),
        pytest.param("https://elsewhere.com", 403, id="disallowed-origin"),
    ],
)
def test_download_embed_key_origin(api_client, experiment, origin, expected_status):
    channel = ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": EMBED_KEY, "allowed_domains": ["example.com"]},
    )
    session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)
    file = _attach(session)

    response = api_client.get(
        _url(session, file),
        HTTP_X_EMBED_KEY=EMBED_KEY,
        HTTP_ORIGIN=origin,
        HTTP_X_SESSION_TOKEN=issue_session_token(session),
    )

    assert response.status_code == expected_status
