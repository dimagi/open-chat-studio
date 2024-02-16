import pytest

from apps.utils.deletion import get_related_m2m_objects


def test_get_related_objects_none():
    assert get_related_m2m_objects([]) == []


@pytest.mark.django_db()
def test_get_related_objects():
    from apps.utils.tests.models import Bot, Param, Tool

    tool1 = Tool.objects.create(name="tool1")
    Param.objects.create(name="param1", tool=tool1)
    tool2 = Tool.objects.create(name="tool2")
    tool3 = Tool.objects.create(name="tool3")

    bot1 = Bot.objects.create(name="bot1")
    bot2 = Bot.objects.create(name="bot2")
    bot3 = Bot.objects.create(name="bot3")

    bot1.tools.set([tool1, tool2])
    bot2.tools.set([tool1])

    assert get_related_m2m_objects([bot1]) == {bot1: {tool1, tool2}}
    assert get_related_m2m_objects([bot1, bot2]) == {bot1: {tool1, tool2}, bot2: {tool1}}
    assert get_related_m2m_objects([bot1], exclude=[tool2]) == {bot1: {tool1}}
    assert get_related_m2m_objects([bot2]) == {bot2: {tool1}}
    assert get_related_m2m_objects([bot3]) == {}
    assert get_related_m2m_objects([tool1]) == {tool1: {bot1, bot2}}
    assert get_related_m2m_objects([tool1, tool2]) == {tool1: {bot1, bot2}, tool2: {bot1}}
    assert get_related_m2m_objects([tool2]) == {tool2: {bot1}}
    assert get_related_m2m_objects([tool3]) == {}
    assert get_related_m2m_objects([tool1], exclude=[bot1]) == {tool1: {bot2}}
