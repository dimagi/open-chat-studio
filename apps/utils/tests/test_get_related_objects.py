import pytest

from apps.utils.deletion import get_related_m2m_objects


@pytest.fixture()
def _model_setup():
    from apps.utils.tests.models import Bot, Param, Tool

    tool1 = Tool.objects.create(name="t1")
    Param.objects.create(name="p1", tool=tool1)

    tool2 = Tool.objects.create(name="t2")
    Tool.objects.create(name="t3")

    bot1 = Bot.objects.create(name="b1")
    bot2 = Bot.objects.create(name="b2")
    Bot.objects.create(name="b3")

    bot1.tools.set([tool1, tool2])
    bot2.tools.set([tool1])


@pytest.mark.django_db()
@pytest.mark.usefixtures("_model_setup")
@pytest.mark.parametrize(
    ("source", "exclude", "expected"),
    [
        ([], None, {}),
        (["b1"], None, {"b1": {"t1", "t2"}}),
        (["b1", "b2"], None, {"b1": {"t1", "t2"}, "b2": {"t1"}}),
        (["b1"], ["t2"], {"b1": {"t1"}}),
        (["b2"], None, {"b2": {"t1"}}),
        (["b3"], None, {}),
        (["t1"], None, {"t1": {"b1", "b2"}}),
        (["t1"], ["b1"], {"t1": {"b2"}}),
        (["t1", "t2"], None, {"t1": {"b1", "b2"}, "t2": {"b1"}}),
        (["t2"], None, {"t2": {"b1"}}),
        (["t3"], None, {}),
    ],
)
def test_get_related_objects(source, exclude, expected):
    from apps.utils.tests.models import Bot, Tool

    if source:
        source_model = Bot if source and source[0][0] == "b" else Tool
        source = source_model.objects.filter(name__in=source)
    if exclude:
        exclude_model = Bot if exclude[0][0] == "b" else Tool
        exclude = exclude_model.objects.filter(name__in=exclude)

    result = get_related_m2m_objects(source, exclude=exclude)
    assert convert_to_name_mapping(result) == expected


def convert_to_name_mapping(result):
    results_by_name = {}
    for key, value in result.items():
        results_by_name[key.name] = {obj.name for obj in value}
    return results_by_name
