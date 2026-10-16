import pytest

from apps.teams.export.translation import FKTranslationStore


@pytest.fixture()
def make_store():
    """Build FKTranslationStores whose sqlite connections are closed on teardown."""
    stores = []

    def _make(path):
        store = FKTranslationStore(path)
        stores.append(store)
        return store

    yield _make
    for store in stores:
        store.close()
