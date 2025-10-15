import pytest

from apps.utils.tests.utils import setup_test_app, tear_down_test_app

app_label = "audit_tests"

# run this outside a hook to ensure it runs before any other test setup
setup_test_app(__package__, app_label)


@pytest.fixture(autouse=True, scope="package")
def _setup_test_app_fixture():
    yield
    tear_down_test_app(app_label)
