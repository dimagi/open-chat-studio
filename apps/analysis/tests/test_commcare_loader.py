from apps.analysis.steps.loaders import CommCareAppLoader


def test_commcare_static_form():
    form_cls = CommCareAppLoader.param_schema().get_static_config_form_class()
    app_list_input = """
    domain1,id1,name1
    domain2,id2,name2
    domain3,id3,name3
    """
    form = form_cls(None, {"app_list": app_list_input}, initial={})
    assert form.is_valid()
    expected = [
        {"domain": "domain1", "app_id": "id1", "name": "name1"},
        {"domain": "domain2", "app_id": "id2", "name": "name2"},
        {"domain": "domain3", "app_id": "id3", "name": "name3"},
    ]
    assert form.cleaned_data == {"app_list": expected}
    assert form.get_params() == CommCareAppLoader.param_schema(app_list=expected)


def test_commcare_dynamic_form():
    form_cls = CommCareAppLoader.param_schema().get_dynamic_config_form_class()
    app_list = [
        {"domain": "domain1", "app_id": "id1", "name": "name1"},
        {"domain": "domain2", "app_id": "id2", "name": "name2"},
        {"domain": "domain3", "app_id": "id3", "name": "name3"},
    ]
    form = form_cls(
        None,
        {"app_id": "id2"},
        initial={"app_list": app_list},
    )
    assert form.is_valid()
    assert form.cleaned_data == {"app_id": "id2"}
    assert form.get_params() == CommCareAppLoader.param_schema(cc_app_id="id2", cc_domain="domain2")
