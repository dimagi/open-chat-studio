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

- A session-authenticated caller may start a session only if they belong to the chatbot's team **or** the request carries that chatbot's widget embed key, validated against the channel's `allowed_domains` exactly as `WidgetDomainPermission` validates it.
- **This binds session-authenticated callers only. Anonymous callers are not required to present an embed key.** A request carrying no credentials at all may still start a session on any chatbot whose `public_id` it knows, exactly as before this ADR. What is being withdrawn is a privilege the Django session cookie was granting on its own — nothing is being added to the anonymous path, which stays governed by the channel's widget auth level (ADR-0045) and the permission classes.
- Selecting a chatbot version stays a team-member capability; an embed key does not grant it. An anonymous caller may not select one either, which predates this ADR.
- The embed key authorizes without changing identity: the caller stays the authenticated user, so the participant remains user-linked and session state is still recorded.
- A widget's own channel owns the session whenever that widget's embed key accompanies the request, whether the key authenticated it or merely rode along. Attribution tracks the widget, not the authenticator.
- `ChatWidgetConfig` derives the embed key from its configured `chatbot_id` rather than storing one; site config cannot name a chatbot that has no embedded widget channel.

## Consequences

- A leaked `public_id` no longer lets an arbitrary *logged-in* user consume another team's chatbot. It does not turn `public_id` into a secret: the same request without a session cookie is still accepted, so this raises the bar against accidental and in-browser misuse, not against a determined caller.
- The anonymous-without-a-key path stays open for now because widgets older than 0.5.1 predate the embed key and cannot send one (`EMBED_KEY_INTRODUCED` in `apps/channels/widget_versions.py`). Closing it is queued behind the deprecation schedule in that module: the current entry deprecates everything below 0.6.0 with a sunset of 1 October 2026, past which no supported widget lacks a key. Those `sunset_at` dates are RFC 8594 declarations of intent rather than enforcement, so dropping the path will be its own change — gated on that date and on the ADR-0045 ratchet having moved live channels off `NONE`.
- The site help widget is no longer a special case: it is an embedded widget that authorizes like any other, so widget version telemetry and the ADR-0045 auth-level ratchet finally see it, and its sessions inherit that channel's ADR-0044 policy.
- Attribution follows the widget rather than the viewer, so a support-team member and an ordinary user on the same page produce one channel and one participant identity, not two.
- Help-widget participants move from `platform=api` to `platform=embedded_widget`. Because `Participant` is keyed on `(team, platform, identifier)`, existing users get a second participant record and their prior conversations stay under the old one.
- The support bot's widget channel must list the OCS host in `allowed_domains` in every environment, or the help widget cannot start a session. The admin configuration form refuses a chatbot without a widget channel, but cannot check the domain.
- A same-origin embed inside OCS may still run on the session cookie alone, as long as its viewers are members of the chatbot's team. `templates/chatbots/single_chatbot_home.html` is exactly that — no `embed-key`, and it picks a version through `openChatWidget(...)`, which only a member may do anyway. The site help widget is the outlier that needs a key, because it renders for every logged-in user regardless of team. An embed whose audience is not guaranteed to be team members must carry the key.
- Embeds that attach to an already-started session by passing `session-id` and `session-token` — `chatbots/chat/web_chat.html` and `chatbots/components/session_chat_widget_launcher.html` — never reach `chat_start_session`, so nothing here applies to them. Their access is ADR-0039's proof-of-possession, not this ADR's.

## Alternatives considered

- **Store an `embed_key` field on `ChatWidgetConfig`** — rejected: a copy of a value the chatbot already owns, silently stale after a token rotation.
- **Order `EmbeddedWidgetAuthentication` before `SessionAuthentication`** — rejected: the key would win over the cookie and downgrade help-widget users to anonymous participants, losing user linkage and the page context that is only saved for authenticated callers.
- **Accept the key as authorization but keep sessions on the team API channel** — rejected: attribution would depend on which credential happened to be consulted, and the site widget would stay invisible to widget telemetry.
- **Switch to the widget channel only when the key was needed** — rejected: membership short-circuits the authorization check, so staff and non-staff on the same page would land on different channels and different participants.
- **Restrict the start endpoint to team members outright** — rejected: it breaks the site help widget with no replacement, and the embed key is the proof external embedders already present.
- **Require the embed key from anonymous callers in the same change** — deferred, not rejected: widgets below 0.5.1 have no key to send, so it would have broken every such embed on deploy. It becomes available once the 0.6.0 sunset above has passed, and is the natural follow-up to this ADR.
