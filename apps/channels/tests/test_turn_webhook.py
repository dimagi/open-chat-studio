import base64
import hashlib
import hmac
import json

import pytest

from apps.channels import turn_webhook

HMAC_SECRET = "turn_test_hmac_secret"


def _sign(payload: bytes, secret: str = HMAC_SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode()


class TestVerifySignature:
    def test_valid_signature(self):
        payload = b'{"messages": [{"id": "abc"}]}'
        assert turn_webhook.verify_signature(payload, _sign(payload), HMAC_SECRET) is True

    def test_signature_from_a_different_secret(self):
        payload = b'{"messages": [{"id": "abc"}]}'
        assert turn_webhook.verify_signature(payload, _sign(payload, "other_secret"), HMAC_SECRET) is False

    def test_tampered_body(self):
        signature = _sign(b'{"messages": [{"id": "abc"}]}')
        assert turn_webhook.verify_signature(b'{"messages": [{"id": "xyz"}]}', signature, HMAC_SECRET) is False

    def test_reserialized_body_does_not_verify(self):
        """Raw-body discipline: re-serializing reorders keys, so only request.body may be signed."""
        raw = b'{"b": 2, "a": 1}'
        reserialized = json.dumps(json.loads(raw), sort_keys=True).encode()
        assert turn_webhook.verify_signature(reserialized, _sign(raw), HMAC_SECRET) is False

    @pytest.mark.parametrize(
        "signature_header",
        [
            pytest.param("", id="empty_header"),
            pytest.param("not-base64-at-all", id="garbage_header"),
            pytest.param("sha256=" + _sign(b'{"messages": []}'), id="prefixed_header"),
            pytest.param("sïgnature-with-non-ascii", id="non_ascii_header"),
        ],
    )
    def test_unusable_header_returns_false_without_raising(self, signature_header):
        assert turn_webhook.verify_signature(b'{"messages": []}', signature_header, HMAC_SECRET) is False

    def test_empty_secret(self):
        payload = b'{"messages": []}'
        assert turn_webhook.verify_signature(payload, _sign(payload), "") is False

    def test_empty_payload_with_matching_signature(self):
        assert turn_webhook.verify_signature(b"", _sign(b""), HMAC_SECRET) is True
