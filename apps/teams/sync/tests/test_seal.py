import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.teams.sync import seal as seal_mod


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return public_pem, private_pem


def test_seal_unseal_round_trips_a_dict(keypair):
    public_pem, private_pem = keypair
    value = {"api_key": "sk-secret", "nested": {"a": 1}}
    token = seal_mod.seal(value, seal_mod.load_public_key(public_pem))
    assert seal_mod.unseal(token, seal_mod.load_private_key(private_pem)) == value


def test_seal_unseal_round_trips_a_string(keypair):
    public_pem, private_pem = keypair
    token = seal_mod.seal("base64-key==", seal_mod.load_public_key(public_pem))
    assert seal_mod.unseal(token, seal_mod.load_private_key(private_pem)) == "base64-key=="


def test_seal_handles_value_larger_than_rsa_block(keypair):
    public_pem, private_pem = keypair
    value = {"blob": "x" * 8000}  # exceeds a 2048-bit RSA direct-encrypt limit
    token = seal_mod.seal(value, seal_mod.load_public_key(public_pem))
    assert seal_mod.unseal(token, seal_mod.load_private_key(private_pem)) == value


def test_token_does_not_leak_plaintext(keypair):
    public_pem, _ = keypair
    token = seal_mod.seal({"api_key": "sk-secret"}, seal_mod.load_public_key(public_pem))
    assert isinstance(token, str)
    assert "sk-secret" not in token


def test_unseal_with_wrong_key_fails(keypair):
    public_pem, _ = keypair
    token = seal_mod.seal({"api_key": "sk-secret"}, seal_mod.load_public_key(public_pem))
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError, match="[Dd]ecryption"):
        seal_mod.unseal(token, wrong)


def test_token_is_compact_base64_envelope(keypair):
    public_pem, _ = keypair
    token = seal_mod.seal({"a": 1}, seal_mod.load_public_key(public_pem))

    envelope = json.loads(base64.b64decode(token))
    assert {"k", "v"} <= set(envelope)
