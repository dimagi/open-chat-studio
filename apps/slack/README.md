# Slack App

This Django app integrates with Slack using the [Slack Bolt framework](https://slack.dev/bolt-python/concepts).
The app supports to connecting OCS with Slack workspaces, handles OAuth authentication, and responds to Slack events.

## How it works

* Slack messaging provider configured using oauth
* Experiment linked to a Slack channel via the channel ID
* Slack events are handled by the app and respond directly to messages (not using Celery)

### ALL channels listener
In addition to linking an experiment to a single channel, there can be one experiment that will respond to mentions
from any channel that it is invited to (provided there isn't already an experiment linked directly to that channel).
