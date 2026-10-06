# Abuse detection

Code-level changes are in [spec-technical.md](spec-technical.md).

## Summary

Every participant message is checked by a cheap built-in rule for markup injection, and files of blocked types are rejected on every channel. Teams that opt in also have each message's text checked by a moderation provider they configure with their own credentials; OpenAI's moderation API is the only provider type for now. The check runs while the bot generates its response, and the response waits for it.

If the provider flags the message in a severe category (such as `sexual/minors` or `illicit/violent`), the bot's response is discarded, and the participant is told their message was flagged. If the team has auto-block on, the participant also gets a strike. If the provider is rate-limited, fails or is too slow, the response is sent and the team is notified.

When a participant reaches 3 strikes, they go on the team's denylist and can no longer use any of the team's chatbots or receive messages from them. Strikes and blocks do not expire; a team member unblocks the participant on the participant page, which also resets their strikes.

The feature is released behind a feature flag.

## Goal

Detect abusive participants on every channel and limit the harm they can do (cost, system prompt or data exposure, XSS, illegal content), without penalising normal users.

## What counts as abuse

| \# | Surface | Type | Severity | Detected by | Action |
| :---- | :---- | :---- | :---- | :---- | :---- |
| 1 | Chat | Harmful content (sexual/minors, illicit, violence, …) | High | Moderation provider | Strike, flag, response replaced |
| 2 | Chat | Trying to get the bot to produce illegal content | High | Moderation provider | Strike, flag, response replaced |
| 3 | Chat | Script / markup injection (`<script>`, `on*=`, `javascript:`) | Low–Medium | Rules | Recorded, no strike |
| 4 | Attachment | Disallowed file type (`.exe`) | n/a | Extension and file type check | Reject the file, no strike |

### Moderation categories

The categories are the [OpenAI moderation categories](https://platform.openai.com/docs/api-reference/moderations/object). The provider decides what counts as flagged; we set no thresholds of our own.

| Category | Description | Action |
| :---- | :---- | :---- |
| `sexual/minors` | Sexual content that includes an individual who is under 18 years old. | Strike, flag, response replaced |
| `illicit` | Instructions or advice that facilitate wrongdoing, e.g. “how to shoplift”. | Strike, flag, response replaced |
| `illicit/violent` | Instructions or advice that facilitate violent wrongdoing, or the procurement of any weapon. | Strike, flag, response replaced |
| `harassment/threatening` | Harassment that also includes violence or serious harm towards any target. | Strike, flag, response replaced |
| `hate/threatening` | Hateful content that also includes violence or serious harm towards a protected group. | Strike, flag, response replaced |
| `sexual` | Content meant to arouse sexual excitement, or that promotes sexual services (excluding sex education and wellness). | No action, not recorded |
| `harassment` | Harassing language towards any target. | No action, not recorded |
| `hate` | Hate based on race, gender, ethnicity, religion, nationality, sexual orientation, disability status, or caste. | No action, not recorded |
| `violence` | Content that depicts death, violence, or physical injury. | No action, not recorded |
| `violence/graphic` | The same, in graphic detail. | No action, not recorded |
| `self-harm`, `self-harm/intent`, `self-harm/instructions` | Content about self-harm. | No action here. These belong to the emergency escalation work in \#3870, which should notify a human rather than penalise the participant. |

A message flagged in several strike categories at once gives one strike. ~~Only strike categories are recorded on the message; a message flagged only in other categories counts as passed.~~
The results across all categories are recorded on the message.

## User stories

### Team admin: setting up moderation

See the mockup here: [https\://claude.ai/artifact/AMRejMSiP7dEii1EqF5AHT?sk=1zIYGKHaJwhcmKvv7sNTyQ](https://claude.ai/artifact/AMRejMSiP7dEii1EqF5AHT?sk=1zIYGKHaJwhcmKvv7sNTyQ)

1. **Add a moderation provider.** As a team admin, I can add a moderation provider (OpenAI) with my team's own API key, so moderation runs on my account's rate limits and costs. The key is checked when I save the provider, and an invalid key is rejected.
2. **Turn moderation on.** As a team admin, I can turn on moderation in a new Moderation section of team settings and select one of my moderation providers. It then applies to all of my team's chatbots.
   - Moderation cannot be turned on without a provider.
   - If the selected provider is deleted, moderation turns off and the team is notified.
3. **Turn auto-block on.** As a team admin, I can turn on auto-block separately, since a block applies to all of my team's chatbots. Strikes are only counted while auto-block is on, and a participant who reaches the limit is blocked. With auto-block off, flagged messages are still flagged and their responses replaced, but give no strike.
4. **See whether my provider keeps up.** As a team admin, I can see how many messages passed, were flagged, or could not be moderated in the last 7 days, so I can tell when my traffic outgrows my provider account.
5. **Get told when moderation fails.** As a team member who can edit chatbots and participants, I get a notification when moderation could not run (rate limit, error, timeout).

### Team member: reviewing abuse

6. **See flagged messages.** As a team member, I can see in the session transcript which messages the provider flagged, with their strike categories, and, marked separately, which messages a rule fired on. A rule firing does not make a message flagged. The session export includes each message's moderation status.
7. **Find flagged sessions.** As a team member, I can filter the session list by moderation status (passed, flagged, skipped), and separately to sessions where a rule fired.
8. **See strikes.** As a team member, I can see a participant's strike count on the participant page.
9. **Block and unblock.** As a team member, I can block a participant manually and unblock them on the participant page. Unblocking resets their strikes to 0\. Manual blocks and unblocks are audit-logged.
10. **See the denylist.** As a team member, I can see all blocked participants in the Moderation section of team settings, with who or what blocked them, why, and when.
11. **Get told about automatic blocks.** As a team member who can edit chatbots and participants, I get a notification when a participant is blocked automatically.

### Participant

12. **Normal use is unaffected.** As a participant, my messages are answered as usual when they are not flagged. If moderation cannot run, I still get my answer.
13. **Flagged message.** As a participant, when my message is flagged in a strike category, I do not get the bot's answer. I get a message in my conversation's language telling me my message was flagged and will not be answered.
14. **Blocked.** As a blocked participant, I cannot use any of the team's chatbots:
    - on the web widget and API, I get an explicit "blocked" response, and I cannot start a new session;
    - on messaging channels, I get no reply;
    - I receive no bot-initiated messages (scheduled messages, reminders, timeout messages). My sessions stay open.
15. **Attachments.** As a participant, a file I send with a disallowed type (for example `.exe`) is rejected on every channel. This gives no strike.

### Chatbot builder

16. **Block from a pipeline.** As a chatbot builder, I can call `block_participant(reason=...)` in a code node to put the current participant on the denylist straight away, without strikes, for a custom safety layer. This adds to the platform checks; it does not replace them.

### Testing

17. **Preview and team members are exempt from penalties.** As a team member testing a chatbot, or in a chatbot preview, my messages are moderated and flagged responses are replaced as for anyone else, but I get no strikes and am never blocked.

## Behaviour rules

- **Strikes** count across all of the team's chatbots, and are only counted while the team has auto-block on. Only the moderation provider's strike categories give strikes. Markup rule hits and attachment rejections do not.
- **Strike limit**: 3, the same for every team.
- **Model refusals** (the bot's own LLM refusing or filtering a turn) are already recorded by \#4599 and give no strike.
- **Moderation failure**: participants get the benefit of the doubt. A message that could not be moderated is answered normally and gives no strike.
- **Slow moderation**: if the provider has not answered within 10 seconds of the bot's response being ready, the response is sent, the message is marked skipped, and a result that arrives later is discarded.
- **Flagged messages** stay in the session and transcript, but the bot does not see them in the conversation history on later turns. The bot's original answer to a flagged message is discarded, not kept for review.
- **Denylist** is per team. A block applies to every chatbot and every published version of it at once, without a publish, and a revert does not undo it.
- **No expiry**: strikes and blocks last until a team member unblocks the participant.
- **Every message with text is moderated** when the team has moderation on. Only the participant's message text is checked (the user query; for a voice note, its transcript); attachments are not sent to the provider, because legitimate content (for example health education images) can be misclassified as explicit.
- **Bot responses are not checked.**
- **Feature flag off**: moderation, the markup rule, strikes and blocks all stop. Attachment rejection is not behind the flag. Existing blocks and strike counts are kept and apply again when the flag is turned back on.
- **Notifications** go to every team member who can edit both chatbots and participants.

## Identity per channel

A block applies to one participant. A participant is unique per team, platform and identifier, so the same identifier on two platforms is two participants and two separate blocks.

| Channel | Reliability of a block |
| :---- | :---- |
| Telegram, WhatsApp, Slack, SMS, email | Stable |
| API (authenticated) | Stable |
| Web widget / anonymous API | Weak |

Why the widget is weak: the participant ID is random and resets when browser storage is cleared, and every new widget session can create a new participant. IPs are shared (NAT, schools, mobile carriers), so blocking by IP can hit unrelated users. Browser fingerprinting has privacy implications and is out of scope. IP-level blocking already exists at the WAF and could be fed by automatic blocks later.

## Why each team brings its own provider

A single account shared by all teams was considered and rejected. The busiest minute in production had 630 participant messages, which needs an OpenAI account on tier 3 or higher. That works for Dimagi's account but not for most self-hosters, and it would put every team's moderation on one quota. With a provider per team, each account only carries that team's traffic. The free tier is enough for a team or self-hosted deployment with low traffic.

## Out of scope

- **Model refusals**: handled by \#4599.
- **Flooding (message rate)**: covered by existing rate limiting (ADR-0052 channel limits and the chat API limits). Rate limiting is separate from blocking a participant.
- **Prompt injection and system prompt extraction**: not in the OpenAI category set, and phrase lists do not work across languages.
- **Harmful attachment content**: attachments are not sent to the provider.

## Future improvements

- **Platform-wide denylist**, as an escalation step above the team denylist: a participant blocked by several teams, or escalated by Dimagi staff, is blocked on every team.
- **Stronger widget identity**: a user ID signed by the embedding site's backend would make widget blocks as reliable as other channels for sites with logged-in users.
- **More moderation provider types**, such as Jev (System One model) or [Laya](https://github.com/NandhaKishorM/laya), a self-hosted classifier, for self-hosters who do not want an external account.
- **Configurable strike limit**, per team or per provider, if teams need it.
- **Prompt injection and system prompt extraction**, as new categories scored by a provider or an extra model call. Proposed actions: strike and flag for injection, flag only for extraction.
- **Attachment moderation**: send images to the provider too, with a way to avoid penalising legitimate content that scores as explicit, such as a per-chatbot opt-out or flag-only for attachments.
