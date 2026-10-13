import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.teams.export import seal as seal_mod
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


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return seal_mod.load_public_key(public_pem), private
