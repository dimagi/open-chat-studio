# Chat Session Token (Server Side, PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require a signed per-session token (or an authenticated user) to access chat-API session endpoints, so a bare session ID no longer grants transcript access.

**Architecture:** Stateless `django.core.signing` token issued at session start; a new `ExperimentSession.session_token_required` flag (default `True`, opt-out at the start endpoint) gates enforcement in a new DRF permission class that replaces `LegacySessionAccessPermission`. Expiry is a sliding inactivity backstop checked against the session's latest message.

**Tech Stack:** Django 5 / DRF, `django.core.signing`, pytest + `time-machine`, FactoryBoy.

**Spec:** `docs/superpowers/specs/2026-06-05-chat-session-token-design.md`

**Worktree:** `/home/skelly/src/open-chat-studio.sk-chat-security` (branch `sk/chat-security`). Run all commands from the worktree root.

---

### Task 1: Token helper module + setting

**Files:**
- Create: `apps/api/session_tokens.py`
- Modify: `config/settings.py` (add `CHAT_SESSION_TOKEN_INACTIVITY_WINDOW`)
- Test: `apps/api/tests/test_session_tokens.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_session_tokens.py`:

```python
from datetime import timedelta

import pytest
import time_machine
from django.core import signing
from django.utils import timezone

from apps.api.session_tokens import (
    SESSION_TOKEN_SALT,
    issue_session_token,
    session_token_expired,
    validate_session_token,
)
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_token_round_trip():
    session = ExperimentSessionFactory.create()
    token = issue_session_token(session)
    assert validate_session_token(token, session.external_id) is True


@pytest.mark.django_db()
def test_tampered_token_rejected():
    session = ExperimentSessionFactory.create()
    token = issue_session_token(session)
    assert validate_session_token(token[:-2] + "xx", session.external_id) is False


def test_garbage_token_rejected():
    assert validate_session_token("not-a-token", "some-id") is False


@pytest.mark.django_db()
def test_token_for_other_session_rejected():
    session = ExperimentSessionFactory.create()
    other = ExperimentSessionFactory.create(experiment=session.experiment)
    token = issue_session_token(other)
    assert validate_session_token(token, session.external_id) is False


@pytest.mark.django_db()
def test_wrong_salt_rejected():
    """A value signed elsewhere in the app with a different salt must not validate."""
    session = ExperimentSessionFactory.create()
    forged = signing.dumps({"sid": str(session.external_id)}, salt="other-salt")
    assert validate_session_token(forged, session.external_id) is False
    # sanity: the real salt is what issue_session_token uses
    assert SESSION_TOKEN_SALT == "ocs.chat.session-token"


@pytest.mark.django_db()
def test_session_not_expired_with_recent_message():
    session = ExperimentSessionFactory.create()
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="hi")
    assert session_token_expired(session) is False


@pytest.mark.django_db()
def test_session_expired_after_inactivity_window():
    session = ExperimentSessionFactory.create()
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="hi")
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        assert session_token_expired(session) is True


@pytest.mark.django_db()
def test_session_with_no_messages_uses_created_at():
    session = ExperimentSessionFactory.create()
    assert session_token_expired(session) is False
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        assert session_token_expired(session) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/api/tests/test_session_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.api.session_tokens'`

- [ ] **Step 3: Add the setting**

In `config/settings.py`, find the chat/file constants (search for `MAX_FILE_SIZE_MB`) and add nearby:

```python
# How long after the last message a chat session token remains usable.
CHAT_SESSION_TOKEN_INACTIVITY_WINDOW = timedelta(days=7)
```

`config/settings.py` already imports `timedelta` for Celery config; if not present at the top, add `from datetime import timedelta`.

- [ ] **Step 4: Write the implementation**

Create `apps/api/session_tokens.py`:

```python
from datetime import datetime

from django.conf import settings
from django.core import signing
from django.db.models import Max
from django.utils import timezone

from apps.experiments.models import ExperimentSession

SESSION_TOKEN_SALT = "ocs.chat.session-token"


def issue_session_token(session: ExperimentSession) -> str:
    """Mint a signed token proving possession of `session`.

    Stateless: the token can be re-derived for any session at any time by
    trusted server-side code (e.g. for bound-session pages).
    """
    return signing.dumps({"sid": str(session.external_id)}, salt=SESSION_TOKEN_SALT)


def validate_session_token(token: str, session_external_id: str) -> bool:
    """Check `token`'s signature and that it was issued for this session."""
    try:
        payload = signing.loads(token, salt=SESSION_TOKEN_SALT)
    except signing.BadSignature:
        return False
    return payload.get("sid") == str(session_external_id)


def session_token_expired(session: ExperimentSession) -> bool:
    """Sliding inactivity backstop: reject token access to long-inactive sessions.

    Activity is the latest chat message (polling does not count, so a leaked
    token cannot keep a session alive), falling back to session creation.
    """
    return timezone.now() - _last_activity(session) > settings.CHAT_SESSION_TOKEN_INACTIVITY_WINDOW


def _last_activity(session: ExperimentSession) -> datetime:
    last_message = session.chat.messages.aggregate(last=Max("created_at"))["last"]
    return last_message or session.created_at
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/api/tests/test_session_tokens.py -v`
Expected: 8 passed

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check apps/api/session_tokens.py apps/api/tests/test_session_tokens.py --fix
uv run ruff format apps/api/session_tokens.py apps/api/tests/test_session_tokens.py
git add apps/api/session_tokens.py apps/api/tests/test_session_tokens.py config/settings.py
git commit -m "Add chat session token helpers"
```

---

### Task 2: `session_token_required` model field + backfill migration

**Files:**
- Modify: `apps/experiments/models.py` (ExperimentSession, near `external_id` at ~line 1369)
- Create: `apps/experiments/migrations/0141_experimentsession_session_token_required.py` (via `makemigrations`, then edit)
- Test: `apps/experiments/tests/test_session_token_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `apps/experiments/tests/test_session_token_backfill.py`:

```python
import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory

migration = importlib.import_module(
    "apps.experiments.migrations.0141_experimentsession_session_token_required"
)


@pytest.mark.django_db()
def test_new_sessions_default_to_token_required():
    session = ExperimentSessionFactory.create()
    assert session.session_token_required is True


@pytest.mark.django_db()
def test_backfill_by_activity():
    stale = ExperimentSessionFactory.create()
    ChatMessage.objects.create(chat=stale.chat, message_type=ChatMessageType.HUMAN, content="old")
    stale_no_messages = ExperimentSessionFactory.create()
    # push both into the past (created_at is auto_now_add, so update via queryset)
    two_days_ago = timezone.now() - timedelta(days=2)
    ExperimentSession.objects.filter(id__in=[stale.id, stale_no_messages.id]).update(created_at=two_days_ago)
    ChatMessage.objects.filter(chat=stale.chat).update(created_at=two_days_ago)

    active = ExperimentSessionFactory.create()
    old_session_recent_message = ExperimentSessionFactory.create()
    ExperimentSession.objects.filter(id=old_session_recent_message.id).update(created_at=two_days_ago)
    ChatMessage.objects.create(
        chat=old_session_recent_message.chat, message_type=ChatMessageType.HUMAN, content="new"
    )

    migration.backfill_session_token_required(django_apps, None)

    def flag(session):
        return ExperimentSession.objects.get(id=session.id).session_token_required

    assert flag(stale) is True
    assert flag(stale_no_messages) is True
    assert flag(active) is False  # created within 24h
    assert flag(old_session_recent_message) is False  # message within 24h
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/experiments/tests/test_session_token_backfill.py -v`
Expected: FAIL with `ModuleNotFoundError` (migration doesn't exist) — and the model field doesn't exist yet either.

- [ ] **Step 3: Add the model field**

In `apps/experiments/models.py`, inside `ExperimentSession` directly after the `external_id` field definition (~line 1369):

```python
    session_token_required = models.BooleanField(
        default=True,
        help_text="Require a signed session token (or authenticated user) for chat API access to this session.",
    )
```

- [ ] **Step 4: Generate and edit the migration**

```bash
uv run python manage.py makemigrations experiments --name experimentsession_session_token_required
```

Then edit the generated `apps/experiments/migrations/0141_experimentsession_session_token_required.py` to add the backfill (final file shape):

```python
from datetime import timedelta

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def backfill_session_token_required(apps, schema_editor):
    """Sessions active in the last 24h keep legacy (token-less) access so live
    conversations are not interrupted; everything older is locked down."""
    ExperimentSession = apps.get_model("experiments", "ExperimentSession")
    cutoff = timezone.now() - timedelta(hours=24)
    recent_ids = ExperimentSession.objects.filter(
        Q(chat__messages__created_at__gte=cutoff) | Q(created_at__gte=cutoff)
    ).values("id")
    ExperimentSession.objects.filter(id__in=recent_ids).update(session_token_required=False)


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0140_drop_experiment_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentsession",
            name="session_token_required",
            field=models.BooleanField(
                default=True,
                help_text="Require a signed session token (or authenticated user) for chat API access to this session.",
            ),
        ),
        migrations.RunPython(backfill_session_token_required, migrations.RunPython.noop),
    ]
```

(If `makemigrations` numbers it other than `0141`, keep the generated number and update the import path in the test.)

- [ ] **Step 5: Run migration and tests**

```bash
uv run python manage.py migrate experiments
uv run pytest apps/experiments/tests/test_session_token_backfill.py -v
```
Expected: 2 passed

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check apps/experiments/ --fix && uv run ruff format apps/experiments/migrations/0141_experimentsession_session_token_required.py apps/experiments/tests/test_session_token_backfill.py
git add apps/experiments/models.py apps/experiments/migrations/ apps/experiments/tests/test_session_token_backfill.py
git commit -m "Add ExperimentSession.session_token_required with activity-based backfill"
```

---

### Task 3: `SessionAccessPermission` enforcement

**Files:**
- Modify: `apps/api/permissions.py` (replace `LegacySessionAccessPermission`, ~line 80)
- Modify: `apps/api/views/chat.py:18,39` (imports + `SESSION_PERMISSION_CLASSES`)
- Test: `apps/api/tests/test_chat_session_token.py` (new)
- Modify (legacy fixtures): `apps/api/tests/test_chat_api_anon.py:19`, `apps/api/tests/test_chat_poll_api.py:22`, `apps/api/tests/test_chat_file_upload_api.py:28`, `apps/api/tests/test_embedded_widget_auth.py:37`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_chat_session_token.py`:

```python
from datetime import timedelta
from unittest import mock

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.api.session_tokens import issue_session_token
from apps.channels.models import ChannelPlatform
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def session(experiment):
    # session_token_required defaults to True
    return ExperimentSessionFactory.create(experiment=experiment)


@pytest.fixture()
def token(session):
    return issue_session_token(session)


def poll_url(session):
    return reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})


@pytest.mark.django_db()
def test_poll_without_token_denied(api_client, session):
    response = api_client.get(poll_url(session))
    assert response.status_code == 403
    assert response.json()["code"] == "session_token_required"


@pytest.mark.django_db()
def test_poll_with_token_allowed(api_client, session, token):
    response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=token)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_poll_with_invalid_token_denied(api_client, session, token):
    response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=token[:-2] + "xx")
    assert response.status_code == 403
    assert response.json()["code"] == "session_token_invalid"


@pytest.mark.django_db()
def test_token_for_other_session_denied(api_client, session):
    other = ExperimentSessionFactory.create(experiment=session.experiment)
    response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=issue_session_token(other))
    assert response.status_code == 403
    assert response.json()["code"] == "session_token_invalid"


@pytest.mark.django_db()
def test_inactive_session_expired(api_client, session, token):
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        response = api_client.get(poll_url(session), HTTP_X_SESSION_TOKEN=token)
    assert response.status_code == 403
    assert response.json()["code"] == "session_expired"


@pytest.mark.django_db()
def test_send_message_requires_token(api_client, session, token):
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    assert api_client.post(url, data={"message": "hi"}, format="json").status_code == 403
    with mock.patch("apps.api.views.chat.get_response_for_webchat_task") as task:
        task.delay.return_value = mock.Mock(task_id="123")
        response = api_client.post(url, data={"message": "hi"}, format="json", HTTP_X_SESSION_TOKEN=token)
    assert response.status_code == 202


@pytest.mark.django_db()
def test_task_poll_requires_token(api_client, session, token):
    url = reverse("api:chat:task-poll-response", kwargs={"session_id": session.external_id, "task_id": "123"})
    assert api_client.get(url).status_code == 403
    with mock.patch("apps.api.views.chat.get_progress_message", return_value=None):
        assert api_client.get(url, HTTP_X_SESSION_TOKEN=token).status_code == 200


@pytest.mark.django_db()
def test_upload_requires_token(api_client, session):
    url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
    response = api_client.post(url, data={})
    assert response.status_code == 403


@pytest.mark.django_db()
def test_legacy_session_skips_token(api_client, experiment):
    legacy = ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)
    assert api_client.get(poll_url(legacy)).status_code == 200


@pytest.mark.django_db()
def test_participant_user_bypasses_token(api_client, session):
    user = session.participant.user
    if user is None:
        user = UserFactory.create()
        session.participant.user = user
        session.participant.save()
    api_client.force_login(user)
    assert api_client.get(poll_url(session)).status_code == 200


@pytest.mark.django_db()
def test_team_member_bypasses_token(api_client, session, team_with_users):
    api_client.force_login(team_with_users.members.first())
    assert api_client.get(poll_url(session)).status_code == 200


@pytest.mark.django_db()
def test_unrelated_user_denied(api_client, session):
    api_client.force_login(UserFactory.create())
    response = api_client.get(poll_url(session))
    assert response.status_code == 403


@pytest.mark.django_db()
def test_embed_key_alone_does_not_bypass_token(api_client, experiment):
    channel = ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "test_widget_token_123456789012", "allowed_domains": ["example.com"]},
    )
    session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)
    response = api_client.get(
        poll_url(session),
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
        HTTP_ORIGIN="https://example.com",
    )
    assert response.status_code == 403
    # but with the token as well it works
    response = api_client.get(
        poll_url(session),
        HTTP_X_EMBED_KEY="test_widget_token_123456789012",
        HTTP_ORIGIN="https://example.com",
        HTTP_X_SESSION_TOKEN=issue_session_token(session),
    )
    assert response.status_code == 200
```

Note: `experiment` and `team_with_users` fixtures come from `apps/conftest.py`. Check `apps/utils/factories/user.py` for the user factory's actual name before running (`grep -n "class.*Factory" apps/utils/factories/user.py`); adjust the import if it differs.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/api/tests/test_chat_session_token.py -v`
Expected: FAIL — anonymous requests currently succeed (`200 != 403`) because `LegacySessionAccessPermission` allows public-experiment access.

- [ ] **Step 3: Implement the permission**

In `apps/api/permissions.py`, replace the whole `LegacySessionAccessPermission` class (lines 80–98) with:

```python
class SessionAccessPermission(BasePermission):
    """Object-capability check for chat session endpoints.

    Token-required sessions demand a valid X-Session-Token (or an
    authenticated user with rights to the session). Legacy sessions
    (session_token_required=False) keep the historical behavior.
    """

    def has_permission(self, request, view):
        session = get_experiment_session_cached(view.kwargs.get("session_id"))
        if not session:
            return False

        if not session.session_token_required:
            return self._has_legacy_access(request, session)

        if request.user.is_authenticated and self._user_has_session_access(request.user, session):
            return True

        token = request.headers.get("X-Session-Token")
        if not token:
            raise exceptions.PermissionDenied(
                detail={"error": "Session token required", "code": "session_token_required"}
            )
        if not validate_session_token(token, session.external_id):
            raise exceptions.PermissionDenied(
                detail={"error": "Invalid session token", "code": "session_token_invalid"}
            )
        if session_token_expired(session):
            raise exceptions.PermissionDenied(detail={"error": "Session has expired", "code": "session_expired"})
        return True

    def _has_legacy_access(self, request, session) -> bool:
        if isinstance(request.auth, ExperimentChannel):
            # widget-authed requests rely on the embed key + domain check
            return True

        experiment = session.experiment
        if experiment.is_public:
            return True

        participant_id = session.participant.identifier
        if not participant_id:
            return False

        return experiment.is_participant_allowed(participant_id)

    def _user_has_session_access(self, user, session) -> bool:
        if session.participant and session.participant.user_id == user.id:
            return True
        return session.team.members.filter(id=user.id).exists()
```

Add the import at the top of `apps/api/permissions.py`:

```python
from apps.api.session_tokens import session_token_expired, validate_session_token
```

(`exceptions` is already imported as `from rest_framework import exceptions`. If importing `apps.api.session_tokens` creates an import cycle via `apps.experiments.models`, move the import inside `has_permission` with a `# avoid circular import` comment — but try module level first.)

In `apps/api/views/chat.py` update line 18 and 39:

```python
from apps.api.permissions import SessionAccessPermission, WidgetDomainPermission
...
SESSION_PERMISSION_CLASSES = [WidgetDomainPermission, SessionAccessPermission]
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest apps/api/tests/test_chat_session_token.py -v`
Expected: all pass

- [ ] **Step 5: Fix existing tests that relied on token-less access**

These fixtures create sessions that the tests access anonymously; pin them to legacy behavior (which the tests are exercising):

`apps/api/tests/test_chat_api_anon.py:19`, `apps/api/tests/test_chat_poll_api.py:22`, `apps/api/tests/test_chat_file_upload_api.py:28` — change the `session` fixture body to:

```python
    return ExperimentSessionFactory.create(experiment=experiment, session_token_required=False)
```

`apps/api/tests/test_embedded_widget_auth.py:37` — add `session_token_required=False` to the `embedded_session` fixture's `ExperimentSessionFactory.create(...)` call.

- [ ] **Step 6: Run the API test suite**

Run: `uv run pytest apps/api/ -v`
Expected: all pass. If other tests fail with 403s on session endpoints, apply the same `session_token_required=False` fixture treatment **only** where the test's purpose is legacy/anonymous access; if the test exercises the start-session flow it will be addressed in Task 4.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check apps/api/ --fix && uv run ruff format apps/api/permissions.py apps/api/views/chat.py apps/api/tests/test_chat_session_token.py
git add -A apps/api/
git commit -m "Enforce session tokens on chat session endpoints"
```

---

### Task 4: Start-endpoint opt-out logic + token issuance

**Files:**
- Modify: `apps/api/serializers.py:233-261` (`ChatStartSessionRequest`, `ChatStartSessionResponse`)
- Modify: `apps/api/views/chat.py:238-341` (`chat_start_session`)
- Modify: `apps/api/tests/test_chat_api_anon.py` (start-session response assertion)
- Regenerate: `api-schemas/v1.yml`
- Test: `apps/api/tests/test_chat_session_token.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/tests/test_chat_session_token.py`:

```python
from apps.experiments.models import ExperimentSession  # add to imports at top


def start_session(api_client, experiment, data_extra=None, **request_kwargs):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, **(data_extra or {})}
    return api_client.post(url, data=data, format="json", **request_kwargs)


@pytest.mark.django_db()
def test_start_session_issues_token_by_default(api_client, experiment):
    response = start_session(api_client, experiment)
    assert response.status_code == 201
    body = response.json()
    assert body["session_token"]
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is True
    # the issued token grants access
    url = reverse("api:chat:poll-response", kwargs={"session_id": body["session_id"]})
    assert api_client.get(url, HTTP_X_SESSION_TOKEN=body["session_token"]).status_code == 200


@pytest.mark.django_db()
def test_start_session_explicit_opt_out(api_client, experiment):
    response = start_session(api_client, experiment, {"use_session_token": False})
    body = response.json()
    assert body["session_token"] is None
    session = ExperimentSession.objects.get(external_id=body["session_id"])
    assert session.session_token_required is False


@pytest.mark.django_db()
def test_start_session_explicit_opt_in_with_widget_header(api_client, experiment):
    response = start_session(
        api_client, experiment, {"use_session_token": True}, HTTP_X_OCS_WIDGET_VERSION="0.9.0"
    )
    body = response.json()
    assert body["session_token"]
    assert ExperimentSession.objects.get(external_id=body["session_id"]).session_token_required is True


@pytest.mark.django_db()
def test_old_widget_implicitly_opts_out(api_client, experiment):
    """Pre-token widgets send the version header but no use_session_token field."""
    response = start_session(api_client, experiment, HTTP_X_OCS_WIDGET_VERSION="0.8.0")
    body = response.json()
    assert body["session_token"] is None
    assert ExperimentSession.objects.get(external_id=body["session_id"]).session_token_required is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/api/tests/test_chat_session_token.py -v -k start_session`
Expected: FAIL with `KeyError: 'session_token'`

- [ ] **Step 3: Update the serializers**

In `apps/api/serializers.py`, add to `ChatStartSessionRequest` (after `participant_name`, ~line 255):

```python
    use_session_token = serializers.BooleanField(
        label="Use session token",
        required=False,
        allow_null=True,
        default=None,
        help_text="Whether to protect the session with a session token (default true). When enabled, the"
        " response includes a `session_token` which must be sent as the `X-Session-Token` header on all"
        " subsequent requests for this session. Set to false to opt out and rely on legacy access rules.",
    )
```

Add to `ChatStartSessionResponse` (~line 261):

```python
    session_token = serializers.CharField(
        label="Session token",
        allow_null=True,
        required=False,
        help_text="Present when the session is token-protected. Send as the `X-Session-Token` header on all"
        " subsequent requests for this session.",
    )
```

- [ ] **Step 4: Update the view**

In `apps/api/views/chat.py` `chat_start_session`, after the `name = data.get("participant_name")` line (~line 248), add:

```python
    use_session_token = data.get("use_session_token")
    if use_session_token is None:
        # Pre-token widgets send the version header but not the field; treat as opt-out.
        use_session_token = "x-ocs-widget-version" not in request.headers
```

After the `session = ApiChannel.start_new_session(...)` call (~line 327), add:

```python
    session_token = None
    if use_session_token:
        session_token = issue_session_token(session)
    else:
        session.session_token_required = False
        session.save(update_fields=["session_token_required"])
```

And include it in `response_data`:

```python
    response_data = {
        "session_id": session.external_id,
        "session_token": session_token,
        "chatbot": experiment_version or experiment,
        "participant": participant,
    }
```

Add the import at the top of `apps/api/views/chat.py`:

```python
from apps.api.session_tokens import issue_session_token
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest apps/api/tests/test_chat_session_token.py -v
uv run pytest apps/api/ -v
```
Expected: the new tests pass. `test_start_chat_session` in `test_chat_api_anon.py` fails on its exact-match response assertion — update its expected dict to include `"session_token": mock.ANY` (the anonymous request has no widget header, so a token is issued). Fix any other start-flow assertions the same way: tests that call start and then hit session endpoints anonymously should either pass the returned token or send `{"use_session_token": False}`, whichever matches what the test is exercising.

- [ ] **Step 6: Regenerate the API schema**

```bash
uv run inv schema
uv run pytest apps/api/tests/test_schema.py -v
```
Expected: schema test passes with the regenerated `api-schemas/v1.yml`.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check apps/api/ --fix && uv run ruff format apps/api/serializers.py apps/api/views/chat.py apps/api/tests/test_chat_session_token.py
git add -A apps/api/ api-schemas/
git commit -m "Issue session tokens at chat session start (opt-out)"
```

---

### Task 5: Full verification

**Files:** none new.

- [ ] **Step 1: Type check**

Run: `uv run ty check apps/`
Expected: no new errors (compare against `git stash; uv run ty check apps/; git stash pop` if unsure).

- [ ] **Step 2: Run the affected test suites**

```bash
uv run pytest apps/api/ apps/experiments/ apps/channels/ -q
```
Expected: all pass. Investigate and fix any failure before proceeding — do not mark this done with failures.

- [ ] **Step 3: Sanity-check migrations**

```bash
uv run python manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`.

- [ ] **Step 4: Commit any stragglers**

```bash
git status --short   # should be clean; commit anything intentional that remains
```

**PR notes (for whoever opens it):** Use `.github/pull_request_template.md`. Migrations ARE backwards compatible (additive field + data backfill). Tick "requires docs/changelog update" — this is a breaking change for direct API consumers of `/api/chat/start/` (token issued and enforced by default; opt out with `use_session_token: false`); the user docs/changelog automation handles the separate docs repo. The widget-side changes are PR 2 (separate, per `docs/superpowers/specs/2026-06-05-chat-session-token-design.md` Delivery section).
