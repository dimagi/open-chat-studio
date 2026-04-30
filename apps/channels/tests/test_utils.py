import pytest
from django.test import override_settings

from apps.channels.utils import get_allowed_email_domains, is_email_domain_allowed


class TestIsEmailDomainAllowed:
    @pytest.mark.parametrize(
        ("allowed_domains", "address", "expected"),
        [
            # Exact match.
            (["example.com", "*.foo.com"], "user@example.com", True),
            # Wildcard matches subdomain.
            (["example.com", "*.foo.com"], "user@mail.foo.com", True),
            # Wildcard does not match the bare domain.
            (["example.com", "*.foo.com"], "user@foo.com", False),
            # Domain not in allowlist.
            (["example.com"], "user@bar.com", False),
            # Empty setting => fail-closed (rejects everything).
            ([], "user@example.com", False),
            # Malformed address — no @.
            (["example.com"], "not-an-email", False),
            # Empty address.
            (["example.com"], "", False),
            # Case-insensitive match.
            (["example.com"], "user@Example.COM", True),
        ],
    )
    def test_matching(self, allowed_domains, address, expected):
        with override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=allowed_domains):
            assert is_email_domain_allowed(address) is expected


class TestGetAllowedEmailDomains:
    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_returns_list(self):
        assert get_allowed_email_domains() == ["example.com", "*.foo.com"]

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_returns_empty_list_when_unset(self):
        assert get_allowed_email_domains() == []
