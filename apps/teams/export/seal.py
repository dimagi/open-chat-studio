"""Envelope encryption for secret fields in transit. A random symmetric key encrypts the value;
the team's RSA public key wraps that symmetric key. This keeps values that exceed RSA's direct
size limit (e.g. a service-account JSON) sealable, and to the caller the result is an opaque token."""

import base64
import json

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def _as_bytes(pem) -> bytes:
    return pem.encode() if isinstance(pem, str) else pem


def load_public_key(pem):
    # seal() encrypts with RSA-OAEP, so reject other key types (EC, Ed25519) at load time -- they'd
    # otherwise blow up with an AttributeError deep inside an export rather than failing fast here.
    key = serialization.load_pem_public_key(_as_bytes(pem))
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("Only RSA public keys are supported for sealing.")
    if key.key_size < 2048:
        raise ValueError("RSA public key must be at least 2048 bits.")
    return key


def load_private_key(pem, password=None):
    key = serialization.load_pem_private_key(_as_bytes(pem), password=password)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("Only RSA private keys are supported for unsealing.")
    return key


def seal(value, public_key) -> str:
    symmetric_key = Fernet.generate_key()
    ciphertext = Fernet(symmetric_key).encrypt(json.dumps(value).encode())
    wrapped_key = public_key.encrypt(symmetric_key, _OAEP)
    envelope = {"k": base64.b64encode(wrapped_key).decode(), "v": ciphertext.decode()}
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def unseal(token: str, private_key):
    envelope = json.loads(base64.b64decode(token))
    symmetric_key = private_key.decrypt(base64.b64decode(envelope["k"]), _OAEP)
    plaintext = Fernet(symmetric_key).decrypt(envelope["v"].encode())
    return json.loads(plaintext)
