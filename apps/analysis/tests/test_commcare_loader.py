from unittest.mock import patch

from apps.analysis.core import PipelineContext, StepContext
from apps.analysis.steps.forms import CommCareAppLoaderParamsForm, CommCareAppLoaderStaticConfigForm
from apps.analysis.steps.loaders import CommCareAppLoader, CommCareAppLoaderParams
from apps.service_providers.auth_service import CommCareAuthService
from apps.service_providers.models import AuthProvider


@patch("apps.analysis.steps.forms._get_auth_provider_queryset", return_value=AuthProvider.objects.none())
def test_commcare_static_form(_):
    app_list_input = """
    domain1,id1,name1
    domain2,id2,name2
    domain3,id3,name3
    """
    form = CommCareAppLoaderStaticConfigForm(
        None, {"app_list": app_list_input, "cc_url": "https://www.commcarehq.org", "auth_provider": 1}, initial={}
    )
    with patch.object(form.fields["auth_provider"], "clean", return_value=AuthProvider(id=1)):
        assert form.is_valid(), form.errors
    expected = [
        {"domain": "domain1", "app_id": "id1", "name": "name1"},
        {"domain": "domain2", "app_id": "id2", "name": "name2"},
        {"domain": "domain3", "app_id": "id3", "name": "name3"},
    ]
    assert form.cleaned_data == {
        "app_list": expected,
        "cc_url": "https://www.commcarehq.org",
        "auth_provider": AuthProvider(id=1),
    }
    assert form.get_params() == CommCareAppLoaderParams(
        app_list=expected, cc_url="https://www.commcarehq.org", auth_provider_id=1
    )


@patch("apps.analysis.steps.forms._get_auth_provider_queryset", return_value=AuthProvider.objects.none())
def test_commcare_static_form_no_apps(_):
    form = CommCareAppLoaderStaticConfigForm(
        None, {"app_list": "", "cc_url": "https://staging.commcarehq.org", "auth_provider": 1}, initial={}
    )
    with patch.object(form.fields["auth_provider"], "clean", return_value=AuthProvider(id=1)):
        assert form.is_valid()
    assert form.get_params() == CommCareAppLoaderParams(
        app_list=[], cc_url="https://staging.commcarehq.org", auth_provider_id=1
    )


def test_commcare_dynamic_form_select():
    app_list = [
        {"domain": "domain1", "app_id": "id1", "name": "name1"},
        {"domain": "domain2", "app_id": "id2", "name": "name2"},
        {"domain": "domain3", "app_id": "id3", "name": "name3"},
    ]
    form = CommCareAppLoaderParamsForm(
        None,
        {"select_app_id": "id2"},
        initial={"app_list": app_list, "cc_url": "https://www.commcarehq.org", "auth_provider_id": 1},
    )
    assert form.is_valid()
    assert form.get_params() == CommCareAppLoaderParams(
        cc_app_id="id2", cc_domain="domain2", cc_url="https://www.commcarehq.org", auth_provider_id=1
    )


def test_commcare_dynamic_form_manual_input():
    form = CommCareAppLoaderParamsForm(
        None,
        {"app_id": "id2", "domain": "domain2"},
        initial={"cc_url": "https://www.commcarehq.org", "auth_provider_id": 1},
    )
    assert form.is_valid()
    assert form.get_params() == CommCareAppLoaderParams(
        cc_app_id="id2", cc_domain="domain2", cc_url="https://www.commcarehq.org", auth_provider_id=1
    )


@patch("apps.analysis.steps.loaders._get_auth_service")
def test_commcare_app_loader(get_auth_service, httpx_mock):
    get_auth_service.return_value = CommCareAuthService(username="user", api_key="key")

    params = CommCareAppLoaderParams(
        cc_app_id="id2", cc_domain="domain2", cc_url="https://www.commcarehq.org", auth_provider_id=1
    )
    loader = CommCareAppLoader(params=params)
    loader.initialize(PipelineContext())

    httpx_mock.add_response(
        url="https://www.commcarehq.org/a/domain2/api/v0.5/application/id2/?format=json",
        json={"app": "data"},
    )

    assert loader(StepContext.initial()).data == '{"app": "data"}'
