import pytest
from field_audit import enable_audit

from apps.utils.tests.utils import setup_test_app, tear_down_test_app

app_label = "utils_tests"

# run this outside a hook to ensure it runs before any other test setup
setup_test_app(__package__, app_label)


@pytest.fixture(autouse=True, scope="package")
def _setup_test_app_fixture():
    yield
    tear_down_test_app(app_label)


@pytest.fixture()
def _model_setup():
    """
    Setup models for testing deletion utilities.

    c1
        -> b1
            -> t1 -> p1, p2
            -> t2
        -> b2 -> t1 -> p1, p2
        -> t1 -> p1, p2
        -> t2
    c2
        -> t2
        -> b3
    t3
    """
    # inline import to avoid importing before app initialization
    from apps.utils.tests.models import Bot, Collection, Param, Tool

    with enable_audit():
        c1 = Collection.objects.create(name="c1")
        c2 = Collection.objects.create(name="c2")

        tool1 = Tool.objects.create(name="t1", collection=c1)
        Param.objects.create(name="p1", tool=tool1)
        Param.objects.create(name="p2", tool=tool1)

        tool2 = Tool.objects.create(name="t2", collection=c2)
        Tool.objects.create(name="t3")

        bot1 = Bot.objects.create(name="b1", collection=c1)
        bot2 = Bot.objects.create(name="b2", collection=c1)
        Bot.objects.create(name="b3", collection=c2)

        bot1.tools.set([tool1, tool2])
        bot2.tools.set([tool1])
