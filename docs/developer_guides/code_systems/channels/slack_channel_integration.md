# Slack Integration on Localhost

This guide helps you set up Slack integration with your environment using a local server exposed via [ngrok](https://ngrok.com/).

Slack can be used as an OCS messaging provider so that participants can interact with the chatbot directly from Slack.
In addition it can be used to deliver [OCS notifications](https://docs.openchatstudio.com/concepts/notifications/) to a team's Slack channel.

## 1. Create a Slack app

1. Visit the [Slack API apps page](https://api.slack.com/apps) and click **Create New App**.
2. Choose **From scratch**.
3. Provide a name for your app and select your workspace.

## 2. Add bot scopes

1. Go to **OAuth & Permissions → Scopes**.
2. Under **Bot Token Scopes**, add the scopes that your app needs, for example:
   - `chat:write` — allows sending messages
   - `files:write` — allows uploading files
3. Click **Save Changes**.

## 3. Install the app to your workspace

- Navigate to **Install App** in the left sidebar.
- Click **Install to Workspace** and authorize.

## 4. Update environment variables

1. Go to the **Basic Information** section of your app.
2. Copy the following values:
   `SLACK_CLIENT_ID`
   `SLACK_CLIENT_SECRET`
   `SLACK_SIGNING_SECRET`
3. Add these to your `.env` file:

   ```env
   SLACK_CLIENT_ID=your-client-id
   SLACK_CLIENT_SECRET=your-client-secret
   SLACK_SIGNING_SECRET=your-signing-secret
   ```

## 5. Install ngrok

1. Download and install ngrok from the [ngrok download page](https://ngrok.com/download).
2. Start your Django server with a public URL using:

   ```bash
   invoke runserver --public
   ```
3. This will expose your local server and generate a public HTTPS URL like:
   ```text
   https://abc123.ngrok.io
   ```

## 6. Update redirect URLs in the Slack app

1. In your Slack app, go to **OAuth & Permissions**.
2. Under **Redirect URLs**, add:

   ```text
   https://<your-ngrok-subdomain>.ngrok.io/slack/oauth_redirect
   ```
3. Click **Save URLs**.

## 7. Set up Slack events endpoint

1. Go to **Event Subscriptions** in your Slack app.
2. Toggle **Enable Events**.
3. Set the **Request URL** to:

   ```text
   https://<your-ngrok-subdomain>.ngrok.io/slack/events
   ```
4. Under **Subscribe to Bot Events**, add:
   - `message.channels`
   - `message.im`
   - Click **Save Changes**.

## 8. Update local Django settings

In your `settings.py`:

```python
SITE_URL_ROOT = "https://<your-ngrok-subdomain>.ngrok.io"

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "<your-ngrok-subdomain>.ngrok.io"]

CSRF_TRUSTED_ORIGINS = ["https://<your-ngrok-subdomain>.ngrok.io"]
```

## 9. Configure the messaging provider in Open Chat Studio

1. Go to **Team Settings → Integrations**.
2. Click **Add integration**, expand **Messaging**, and select **Slack**.
3. Click **Connect Slack** — you'll be redirected to Slack's authorization screen.

See the user guide on [Messaging Providers](https://docs.openchatstudio.com/concepts/team/messaging_providers/).

## 10. Authorize in Slack

- Slack may auto-select a workspace.
  If the correct workspace isn't shown, open the link in **Incognito mode** or **clear cookies**.
- You'll be redirected to your **ngrok URL** (not `localhost`).
- Log back into Open Chat Studio if prompted.
- Navigate again to **Team Settings → Integrations** and complete the Slack setup.

## What this enables

With Slack connected to your local environment, OCS Admin users can:

1. [Deploy a chatbot to Slack](https://docs.openchatstudio.com/how-to/channels/deploy_slack_channel/).
2. Set up [notifications to be sent to Slack](https://docs.openchatstudio.com/how-to/send_notifications_to_slack/).
