"""Tests for diff_allauth_overrides.py.

Run with: uv run pytest scripts/test_diff_allauth_overrides.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from diff_allauth_overrides import context_vars, functional_signals  # noqa: E402


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        pytest.param("{{ authenticator.wrap.secret }}", {"authenticator"}, id="dotted-lookup-root"),
        pytest.param("{% if is_mfa_enabled %}x{% endif %}", {"is_mfa_enabled"}, id="condition"),
        pytest.param("{% for code in unused_codes %}{{ code }}{% endfor %}", {"unused_codes"}, id="loop-target-bound"),
        pytest.param("{% if user.emailaddress_set.all %}x{% endif %}", set(), id="relation-on-user"),
        pytest.param('{% url "account_login" as login_url %}{{ login_url }}', set(), id="bound-by-url-as"),
        pytest.param('{% if process == "signup" %}x{% endif %}', {"process"}, id="quoted-string-ignored"),
        pytest.param("{{ form.secret }}{{ form.errors }}", set(), id="form-attributes"),
    ],
)
def test_context_vars(template, expected):
    assert context_vars(template) == expected


def test_functional_signals():
    template = """
      {% url 'mfa_download_recovery_codes' %}
      <input name="action_remove">
      {{ form.password }}
    """
    signals = functional_signals(template)

    assert signals["urls"] == {"mfa_download_recovery_codes"}
    assert signals["form actions"] == {"action_remove"}
    assert signals["form fields"] == {"password"}
