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
* Check invitation and signup flows
