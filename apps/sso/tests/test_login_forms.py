import pytest

from apps.sso.views import LoginEmailForm, LoginPasswordForm


@pytest.mark.parametrize(
    ("form_class", "field"),
    [
        pytest.param(LoginEmailForm, "login", id="email-step"),
        pytest.param(LoginPasswordForm, "password", id="password-step"),
    ],
)
def test_login_field_is_autofocused(form_class, field):
    assert form_class().fields[field].widget.attrs["autofocus"] is True


@pytest.mark.parametrize(
    ("form_class", "field", "autocomplete"),
    [
        pytest.param(LoginEmailForm, "login", "email", id="email-step"),
        pytest.param(LoginPasswordForm, "password", "current-password", id="password-step"),
    ],
)
def test_login_field_autocomplete(form_class, field, autocomplete):
    assert form_class().fields[field].widget.attrs["autocomplete"] == autocomplete


def test_password_field_is_rendered_with_attrs():
    """The attrs are only useful if they survive rendering."""
    rendered = str(LoginPasswordForm()["password"])
    assert 'autocomplete="current-password"' in rendered
    assert "autofocus" in rendered
    assert 'type="password"' in rendered
