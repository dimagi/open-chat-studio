# ADR-0063: Session start requires a client-credentials token with the `chat:start` scope

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-02</p>

<p class="adr-meta">Extends: <a href="0056-client-credentials-applications-name-their-chatbots.md">ADR-0056</a>, <a href="0059-chat-api-channel-credential-mode.md">ADR-0059</a></p>

## Context

ADR-0056 limits which chatbots a machine token may reach. The scope determines what the token may do. The only conversational scope was `chatbots:interact`, which applies team-wide and also authorises outbound messages to arbitrary participants (ADR-0056). The supported browser embed places a bearer token in page JavaScript, so using that scope would replace a channel-scoped embed key with a team-wide messaging credential.

Authorization-code tokens derive their team from a grant and a membership check. Admitting them would require deciding whether a signed-in user's token may chat as an anonymous participant, which this work does not need to decide.

## Decision

We will admit a token at `POST /api/chat/start/` only when all of the following hold.

- It validates through django-oauth-toolkit: signature, expiry, revocation.
- Its application uses the client-credentials grant.
- Its team is the chatbot's team.
- It carries `chat:start`, a new scope registered in `OAUTH2_PROVIDER["SCOPES"]` and `OAUTH_CLIENT_CREDENTIALS_SCOPES`, and named by the `CHAT_API_SCOPE` setting.
- The chatbot's Chat API Channel is in `oauth` mode (ADR-0059).
- The token's application lists the chatbot (ADR-0056), normalised to the working version.

The scope check uses the same subset semantics as every other endpoint. `chat:start` is required but not exclusive: a token with `chatbots:interact chat:start` is admitted, and a token with `chatbots:interact` alone is refused.

## Consequences

- A server-only integration must request one additional scope. No existing caller is affected, because no OAuth caller could reach this endpoint before.
- The scope authorises one endpoint, because the session-bound endpoints use the session token (ADR-0061).
- The scope makes a narrow page token possible but does not enforce one. There is no per-application scope allowlist, so a broad-scope token placed in a page is also accepted here. The integration documentation must instruct hosts to request `chat:start` alone for any token that reaches a browser.
- Authorization-code tokens are refused. The grant-type check exists, so admitting them later is a small change.
- A version's own `public_id` returns 404 at this endpoint (ADR-0061). Version addressing is available on the `chatbots:interact` endpoints.
- The allowlist denial here is a 401, not the 403 of ADR-0056 (ADR-0062).

## Alternatives considered

- **Reuse `chatbots:interact`** → rejected: it gives a browser a team-wide outbound-messaging credential.
- **Require `chat:start` exclusively** → rejected: it would be the only endpoint not using subset semantics, and a host that also uses chat completions would need a second token.
- **Admit authorization-code tokens** → deferred: it raises a participant-identity question this work does not need to settle.
- **Restrict the chatbot with the embed key in addition to the token** → rejected in ADR-0059.
