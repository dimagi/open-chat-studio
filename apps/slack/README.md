# Slack App

This Django app integrates with Slack using the [Slack Bolt framework](https://slack.dev/bolt-python/concepts).
The app supports to connecting OCS with Slack workspaces, handles OAuth authentication, and responds to Slack events.

## How it works

* Slack messaging provider configured using oauth
* Experiment linked to a Slack channel via the channel ID
* Slack events are handled by the app and respond directly to messages (not using Celery)
* The bot will only respond to messages if it is directly mentioned or if the message is part of a slakc thread
  that is linked to a session (see [below](#slack-threads-and-experiment-sessions) on threads and sessions).

### ALL channels listener

In addition to linking an experiment to a single channel, there can be one experiment that will respond to mentions
from any channel that it is invited to (provided there isn't already an experiment linked directly to that channel).

### Slack Threads and Experiment Sessions

The app uses Slack threads to link messages to an experiment session. When a user starts a thread with the bot, the bot
will create a new session and link the thread to that session. The bot will then respond to messages in that thread
as if they were part of the same session. This allows the bot to maintain state between messages in a thread.

This model is more like that of Web sessions since a user can start a new session (thread) at any time or continue
an existing session by replying to a message in the thread.

For this reason the experiment session management is handled by the Slack app itself, rather than by the
`SlackChannel`.

If a user mentions the bot in an existing thread (that isn't already linked to a session) then the bot will create a new
session and link the thread to that session (though only messages after the bot mention will be part of the session
history).
