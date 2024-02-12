from apps.service_providers.models import AuthProviderType


def test_commcare_auth_provider(team_with_users):
    _test_provider(
        team_with_users,
        AuthProviderType.commcare,
        data={
            "username": "demo",
            "api_key": "123123",
        },
    )


def _test_provider(team, provider_type: AuthProviderType, data):
    form = provider_type.form_cls(data=data)
    assert form.is_valid()
    assert form.cleaned_data == data
