# OAuth Applications

An OAuth application is a registration that lets an external system authenticate to Open Chat Studio.
Most are registered by team admins and belong to a single team. A **global** application belongs to no
team and can be registered only by a superuser — that is what this guide covers.

## Team-scoped vs. global applications

|  | Team-scoped | Global |
| --- | --- | --- |
| Registered from | Team Settings > OAuth Applications (`/a/<team-slug>/oauth/applications/`) | Global Admin > OAuth Apps (`/o/global-applications/`) |
| Who can register it | Team admins | Superusers only |
| Grant types | Authorization code, or client credentials | **Authorization code only** |
| Who can authorize it | Members of that one team | Any user, in any of *their* teams |
| Team a token is scoped to | Always the application's team | The team the authorizing user picks |

Register a global application when one external integration needs to serve users across many teams —
a partner portal, or a system that signs users in through OCS — and you do not want a separate
client ID and secret per team.

Everything else should be team-scoped. A team-scoped application is self-service, and it keeps the
blast radius of a leaked secret inside one team.

## Why global applications are authorization-code only

A global application has no team of its own, so something has to decide which team a token may reach.
For the authorization-code grant that is the user: they sign in, and the consent screen asks them to
pick one of their teams.

The client-credentials grant has no user — nobody to ask — so a global client-credentials application
would have no team at all, and no way to derive one. The registration form therefore offers only the
authorization-code grant, and the choice is locked.

## Registering one

1. Sign in as a superuser and go to **Global Admin** (`/admin/`).
2. Click **OAuth Apps**. This button is only rendered for superusers, and the pages behind it return
   404 to everyone else rather than a permission error.
3. Click **Register**, then fill in:
    - **Name** — what the user sees on the consent screen. Make it recognisable; the user is
      consenting to hand this application access to their team's data.
    - **Redirect URIs** — one per line. Required. Only these URIs can receive an authorization code.
    - **Post logout redirect URIs** and **Allowed origins** (CORS) — optional.
    - **Client ID** and **Client secret** are pre-filled with generated values.
4. **Copy the client secret before saving.** It is hashed on save and cannot be retrieved afterwards.
   The edit form does not show it, so there is no way to look it up or rotate it later — if it is
   lost, delete the application and register a new one.

Grant type, algorithm (RS256) and client type (confidential) are fixed and not editable.

## What a token is scoped to

This is the part most easily misread. The *application* is global; a *token* is not.

Each access token is scoped to exactly one team — the one selected when it was authorized — and that
scoping is carried through the whole chain: the authorization code, the access token, and every
refresh of it. An API request made with the token sees only that team's data.

So one global application serves every team, but a client that needs a second team's data must send
the user through the authorization flow again and have them pick that team. There is no token that
spans teams.

Team membership is checked at authorization time, and again when the consent form is submitted. A
user can never scope a token to a team they do not belong to.

## Preselecting the team

Add a `team` query parameter to the authorize URL to preselect the team and hide the picker:

```text
/o/authorize/?client_id=<client-id>&response_type=code&team=<team-slug>&scope=...
```

This is the right thing to do when the client already knows which team the user is acting in — it
removes a decision the user cannot get right without context. If the user is not a member of the
named team, the parameter is ignored and the picker is shown as usual.

## Related settings

Global applications use the same provider configuration as every other OAuth application:

- `OIDC_RSA_PRIVATE_KEY` must be set for token issuance to work at all.
- `OAUTH_PKCE_REQUIRED` defaults to `True`.
- Available scopes are listed under `OAUTH2_PROVIDER["SCOPES"]`; the `openid` and `profile` scopes
  exist only when OIDC is enabled.

See [Configuration](../hosting/configuration.md) for these, and
[Rate Limiting](../hosting/rate_limiting.md) for the limits on `/o/token/`.
