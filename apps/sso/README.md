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

## TODO

* Allow specific emails to bypass the SSO check (useful for testing or if something goes wrong)
* Do we need to associate the `SocialApplication` with a specific `Team` (one or many)?
* Maybe a team setting to restrict invitations to only those with specific email domains?
  * (this is similar to the point above)
* How should we handle signups outside of the invitation flow?
  * Should there be a way to force emails with a specific domain to go through the SSO flow?

## Feature Flag

This is currently behind a feature flag. To test it the FF must have `everyone` set to `Unknown` and have the `Testing` flag on.

Then navigate to the login page and append this to the URL: `?dwft_sso_login=1`. From that point on the SSO flag will be enabled for you. To disalbe it change the `1` to a `0` in the URL param, and it will get disabled. This works using cookies so if you clear your cookies you will need to re-enable it.
