import json
from collections import OrderedDict

import pytest
from django.test import Client
from django.urls import reverse
from jinja2 import TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SecurityError
from pydantic_core import ValidationError

from apps.pipelines.nodes.nodes import RenderTemplate, SendEmail, format_jinja_error
from apps.utils.factories.team import TeamWithUsersFactory


class TestFormatJinjaError:
    def test_undefined_error_with_context(self):
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "subject", context={"input": "", "temp_state": {}})
        assert 'UndefinedError in field "subject"' in result
        assert "Available variables: input, temp_state" in result

    def test_undefined_error_without_context(self):
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "body")
        assert 'UndefinedError in field "body"' in result
        assert "Available variables" not in result

    def test_syntax_error(self):
        exc = TemplateSyntaxError("unexpected '}'", lineno=3)
        result = format_jinja_error(exc, "template_string")
        assert 'TemplateSyntaxError in field "template_string"' in result
        assert "(line 3)" in result

    def test_syntax_error_no_lineno(self):
        exc = TemplateSyntaxError("unexpected end of template", lineno=None)
        result = format_jinja_error(exc, "body")
        assert 'TemplateSyntaxError in field "body"' in result
        assert "(line" not in result

    def test_security_error(self):
        exc = SecurityError("access to attribute 'mro' of 'type' object is unsafe")
        result = format_jinja_error(exc, "body")
        assert 'SecurityError in field "body"' in result

    def test_generic_exception(self):
        exc = ValueError("something broke")
        result = format_jinja_error(exc, "body")
        assert 'Jinja2 error in field "body"' in result
        assert "ValueError" in result

    def test_context_keys_preserve_insertion_order(self):
        """Available variables should appear in insertion order, not sorted."""

        ctx = OrderedDict([("zebra", 1), ("alpha", 2), ("middle", 3)])
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "body", context=ctx)
        assert "Available variables: zebra, alpha, middle" in result


class TestJinjaSyntaxBackstopValidator:
    def test_render_template_valid_syntax(self):
        node = RenderTemplate(name="test", node_id="1", django_node=None, template_string="{{ foo }}")
        assert node.template_string == "{{ foo }}"

    def test_render_template_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            RenderTemplate(name="test", node_id="1", django_node=None, template_string="{{ foo }")

    def test_render_template_empty_string(self):
        """Empty strings are valid — parse("") succeeds."""
        node = RenderTemplate(name="test", node_id="1", django_node=None, template_string="")
        assert node.template_string == ""

    def test_send_email_body_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            SendEmail(
                name="email",
                node_id="1",
                django_node=None,
                recipient_list="test@example.com",
                subject="Hi",
                body="{% if foo %}oops",
            )

    def test_send_email_subject_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            SendEmail(
                name="email",
                node_id="1",
                django_node=None,
                recipient_list="test@example.com",
                subject="{{ broken }",
            )

    def test_send_email_recipient_list_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            SendEmail(
                name="email",
                node_id="1",
                django_node=None,
                recipient_list="{{ broken }",
                subject="Hi",
            )

    def test_send_email_recipient_list_jinja_comment_is_allowed(self):
        """Jinja comments like {# primary #} should not trigger email validation."""
        node = SendEmail(
            name="email",
            node_id="1",
            django_node=None,
            recipient_list="{# primary #}ops@example.com",
            subject="Hi",
            body="Hello",
        )
        assert "{#" in node.recipient_list

    def test_send_email_valid_jinja_fields(self):
        node = SendEmail(
            name="email",
            node_id="1",
            django_node=None,
            recipient_list="{{ participant_data.email }}",
            subject="Hello {{ input }}",
            body="Body: {{ input }}",
        )
        assert "participant_data.email" in node.recipient_list


@pytest.mark.django_db()
class TestValidateJinjaEndpoint:
    @pytest.fixture()
    def team_with_users(self):
        return TeamWithUsersFactory.create()

    @pytest.fixture()
    def authed_client(self, team_with_users):
        client = Client()
        user = team_with_users.members.first()
        client.force_login(user)
        return client

    def _url(self, team):
        return reverse("pipelines:validate_jinja", kwargs={"team_slug": team.slug})

    def _post(self, client, team, data):
        return client.post(
            self._url(team),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_valid_template(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "Hello {{ name }}"})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_empty_template(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": ""})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_jinja_syntax_error(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "{{ foo }"})
        assert response.status_code == 200
        data = response.json()
        assert len(data["errors"]) >= 1
        error = data["errors"][0]
        assert error["severity"] == "error"
        assert error["line"] is not None
        assert "message" in error

    def test_unclosed_html_tag(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "<div><p>{{ foo }}</div>"})
        assert response.status_code == 200
        data = response.json()
        warnings = [e for e in data["errors"] if e["severity"] == "warning"]
        assert len(warnings) >= 1
        assert any("H025" in w["message"] for w in warnings)

    def test_valid_html_no_warnings(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "<div><p>{{ foo }}</p></div>"})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_excluded_djlint_rules_not_reported(self, authed_client, team_with_users):
        """H006 (img height/width) and H013 (img alt) should be filtered out."""
        response = self._post(authed_client, team_with_users, {"template": '<img src="test.png">'})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_checks_jinja_only(self, authed_client, team_with_users):
        """When checks=["jinja"], HTML lint warnings should not be returned."""
        response = self._post(
            authed_client,
            team_with_users,
            {"template": "<div><p>{{ foo }}</div>", "checks": ["jinja"]},
        )
        assert response.status_code == 200
        # No HTML warnings — only Jinja checks were requested, and this is valid Jinja
        assert response.json()["errors"] == []

    def test_checks_html_only(self, authed_client, team_with_users):
        """When checks=["html"], Jinja syntax errors should not be returned."""
        response = self._post(
            authed_client,
            team_with_users,
            {"template": "{{ foo }", "checks": ["html"]},
        )
        assert response.status_code == 200
        # No Jinja error — only HTML checks were requested
        errors = [e for e in response.json()["errors"] if e["severity"] == "error"]
        assert errors == []

    def test_checks_defaults_to_both(self, authed_client, team_with_users):
        """When checks is omitted, both Jinja and HTML checks run."""
        response = self._post(
            authed_client,
            team_with_users,
            {"template": "<div><p>{{ foo }}</div>"},
        )
        assert response.status_code == 200
        warnings = [e for e in response.json()["errors"] if e["severity"] == "warning"]
        assert len(warnings) >= 1

    def test_missing_template_field(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {})
        assert response.status_code == 400

    def test_non_string_template(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": 123})
        assert response.status_code == 400

    def test_checks_not_a_list(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "hello", "checks": "jinja"})
        assert response.status_code == 400

    def test_checks_with_non_string_entries(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "hello", "checks": [{}]})
        assert response.status_code == 400

    def test_checks_with_unknown_value(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "hello", "checks": ["unknown"]})
        assert response.status_code == 400

    def test_unauthenticated_request(self, team_with_users):
        client = Client()
        response = client.post(
            self._url(team_with_users),
            data=json.dumps({"template": "{{ foo }}"}),
            content_type="application/json",
        )
        # login_and_team_required redirects unauthenticated users
        assert response.status_code == 302
