# ADR-0053: Starting a chat session requires team membership or the embed key

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-08-11</p>

<p class="adr-meta">Extends: <a href="0039-require-proof-of-possession-for-chat-session-access.md">ADR-0039</a></p>

## Context

ADR-0039 required proof of possession on the four session endpoints but left the start endpoint (`chat_start_session`) alone. There, an authenticated Django session was sufficient on its own: any logged-in OCS user who knew a chatbot's `public_id` could start a session against any team's chatbot, and only version selection checked team membership. Chatbot `public_id`s are not secret — they ship in every embed snippet.

Closing that hole by requiring team membership would have broken OCS's own site help widget, which `templates/web/app/app_base.html` renders for every logged-in user from `ChatWidgetConfig`. Its users are logged in but are generally not members of the support bot's team, so it worked only because the endpoint accepted a bare session cookie. The config already names the chatbot, so the chatbot's embed key is derivable from it — the widget can present the same proof an external embedder does.

The two credentials then collide. DRF stops at the first authenticator that matches, and `SessionAuthentication` precedes `EmbeddedWidgetAuthentication`, so a same-origin widget's cookie always wins and `request.auth` is never the channel. The embed key has to be consulted by the view rather than inferred from `request.auth`, and once it is, the channel that owns the resulting session is an open question.

## Decision

We will treat being logged in as identity, not as access to every chatbot.

- A session-authenticated caller may start a session only if they belong to the chatbot's team **or** the request carries that chatbot's widget embed key, validated against the channel's `allowed_domains` exactly as `WidgetDomainPermission` validates it. Anonymous callers are unaffected.
- Selecting a chatbot version stays a team-member capability; an embed key does not grant it.
- The embed key authorizes without changing identity: the caller stays the authenticated user, so the participant remains user-linked and session state is still recorded.
- A widget's own channel owns the session whenever that widget's embed key accompanies the request, whether the key authenticated it or merely rode along. Attribution tracks the widget, not the authenticator.
- `ChatWidgetConfig` derives the embed key from its configured `chatbot_id` rather than storing one; site config cannot name a chatbot that has no embedded widget channel.

## Consequences

- A leaked `public_id` no longer lets an arbitrary logged-in user consume another team's chatbot.
- The site help widget is no longer a special case: it is an embedded widget that authorizes like any other, so widget version telemetry and the ADR-0045 auth-level ratchet finally see it, and its sessions inherit that channel's ADR-0044 policy.
- Attribution follows the widget rather than the viewer, so a support-team member and an ordinary user on the same page produce one channel and one participant identity, not two.
- Help-widget participants move from `platform=api` to `platform=embedded_widget`. Because `Participant` is keyed on `(team, platform, identifier)`, existing users get a second participant record and their prior conversations stay under the old one.
- The support bot's widget channel must list the OCS host in `allowed_domains` in every environment, or the help widget cannot start a session. The admin configuration form refuses a chatbot without a widget channel, but cannot check the domain.
- Any future same-origin embed of an OCS chatbot must carry its embed key; a session cookie alone is no longer enough.

## Alternatives considered

- **Store an `embed_key` field on `ChatWidgetConfig`** — rejected: a copy of a value the chatbot already owns, silently stale after a token rotation.
- **Order `EmbeddedWidgetAuthentication` before `SessionAuthentication`** — rejected: the key would win over the cookie and downgrade help-widget users to anonymous participants, losing user linkage and the page context that is only saved for authenticated callers.
- **Accept the key as authorization but keep sessions on the team API channel** — rejected: attribution would depend on which credential happened to be consulted, and the site widget would stay invisible to widget telemetry.
- **Switch to the widget channel only when the key was needed** — rejected: membership short-circuits the authorization check, so staff and non-staff on the same page would land on different channels and different participants.
- **Restrict the start endpoint to team members outright** — rejected: it breaks the site help widget with no replacement, and the embed key is the proof external embedders already present.
