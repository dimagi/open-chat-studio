# SSO Login

This builds on the functionality provided by django-allauth to provide a single sign-on (SSO) solution for the platform.

## Current workings

The current implementation is based on the following:

When a user signs in we check their email address to see if there is a `SocialApplication` configured for their email domain. This is done by adding the list of email domains to the `SocialApplication.settings` field:

```json
{
  "email_domains": ["example.com"]
}
```

On successful login we record the SSO session ID in the `SsoSession` model. This is needed to support single signout.
  See https://openid.net/specs/openid-connect-frontchannel-1_0.html
  See https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc#single-sign-out

## Feature Flag

This is currently behind a feature flag. To test it the FF must have `everyone` set to `Unknown` and have the `Testing` flag on.

Then navigate to the login page and append this to the URL: `?dwft_sso_login=1`. From that point on the SSO flag will be enabled for you. To disalbe it change the `1` to a `0` in the URL param, and it will get disabled. This works using cookies so if you clear your cookies you will need to re-enable it.

## TODO

* Allow specific emails to bypass the SSO check (useful for testing or if something goes wrong)
* Do we need to associate the `SocialApplication` with a specific `Team` (one or many)?
* Maybe a team setting to restrict invitations to only those with specific email domains?
  * (this is similar to the point above)
* How should we handle signups outside of the invitation flow?
  * Should there be a way to force emails with a specific domain to go through the SSO flow?

## Provider notes

### Microsoft Azure

1. Configure the app in Azure

* Authentiction config
  * Web Redirect URL: <http://localhost:8000>/accounts/microsoft/login/callback/
  * Front channel logout URL: <http://localhost:8000>/accounts/sso/logout/
  * Supported account types: "Accounts in this organizational directory only"
* Add the `login_hint` optional claim under 'Token Configuration'

2. Update settings:

```python settings.py
INSTALLED_APPs = [..., "allauth.socialaccount.providers.microsoft"]

SOCIALACCOUNT_PROVIDERS = {
   "microsoft": {
       "SCOPE": ["openid", "profile", "email", "User.Read"],
       "AUTH_PARAMS": {"claims": '{"id_token": {"login_hint": null}}'},
   }

}
```

3. Create a `SocialAccount`

| Field | Value |
| --- | --- |
| Provider Type | "microsoft" |
| Provider ID | anything |
| Client ID | App Client ID |
| Client secret | App Client Secret |
| Settings | {"email_domains": ["dimagi.com"], "tenant": "app tenant ID"}

