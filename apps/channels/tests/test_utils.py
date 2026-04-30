from django.test import override_settings

from apps.channels.utils import get_allowed_email_domains, is_email_domain_allowed


class TestEmailDomainAllowlist:
    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_exact_match_allowed(self):
        assert is_email_domain_allowed("user@example.com") is True

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_wildcard_match_allowed(self):
        assert is_email_domain_allowed("user@mail.foo.com") is True

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_bare_domain_does_not_match_wildcard(self):
        # *.foo.com matches subdomains, not the bare domain.
        assert is_email_domain_allowed("user@foo.com") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com"])
    def test_disallowed_domain_rejected(self):
        assert is_email_domain_allowed("user@bar.com") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_empty_setting_rejects_everything(self):
        assert is_email_domain_allowed("user@example.com") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com"])
    def test_malformed_address_rejected(self):
        assert is_email_domain_allowed("not-an-email") is False
        assert is_email_domain_allowed("") is False

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com"])
    def test_case_insensitive_match(self):
        assert is_email_domain_allowed("user@Example.COM") is True

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_get_allowed_email_domains_returns_list(self):
        assert get_allowed_email_domains() == ["example.com", "*.foo.com"]

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_get_allowed_email_domains_returns_empty_list_when_unset(self):
        assert get_allowed_email_domains() == []
