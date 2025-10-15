# Slack Integration on Localhost

This guide helps you set up Slack integration with your development environment using a local server exposed via ngrok.

## 1. Create a Slack App

1. Visit [https://api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
2. Choose **From scratch**.
3. Provide a name for your app and select your workspace.

## 2. Add Bot Scopes

1. Go to **OAuth & Permissions → Scopes**.
2. Under **Bot Token Scopes**, add the scopes that your app needs, for example:
   - `chat:write` — allows sending messages
   - `files:write` — allows uploading files
3. Click **Save Changes**.

## 3. Install the App to Your Workspace

- Navigate to **Install App** in the left sidebar.
- Click **Install to Workspace** and authorize.

## 4. Update Environment Variables

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

1. Download and install ngrok from [https://ngrok.com/download](https://ngrok.com/download).
2. Start your Django server with a public URL using:

   ```bash
   invoke runserver --public
   ```
3. This will expose your local server and generate a public HTTPS URL like:
   ```
   https://abc123.ngrok.io
   ```

## 6. Update Redirect URLs in Slack App

1. In your Slack app, go to **OAuth & Permissions**.
2. Under **Redirect URLs**, add:

   ```
   https://<your-ngrok-subdomain>.ngrok.io/slack/oauth_redirect
   ```
3. Click **Save URLs**.

## 7. Set Up Slack Events Endpoint

1. Go to **Event Subscriptions** in your Slack app.
2. Toggle **Enable Events**.
3. Set the **Request URL** to:

   ```
   https://<your-ngrok-subdomain>.ngrok.io/slack/events
   ```
4. Under **Subscribe to Bot Events**, add:
   - `message.channels`
   - `message.im`
   - Click **Save Changes**.

## 8. Update Local Django Settings

In your `settings.py`:

```python
SITE_URL_ROOT = "https://<your-ngrok-subdomain>.ngrok.io"

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "<your-ngrok-subdomain>.ngrok.io"
]

CSRF_TRUSTED_ORIGINS = [
    "https://<your-ngrok-subdomain>.ngrok.io"
]
```

## 9. Configure Messaging Provider in Open Chat Studio

1. Go to **Team Settings → Messaging Providers**.
2. Click **Add**, and select **Slack** as the provider type.
3. Click **Connect Slack** — you'll be redirected to Slack's authorization screen.

## 10. Authorize in Slack

- Slack may auto-select a workspace. If the correct workspace isn't shown: 
Open the link in **Incognito mode** or **clear cookies**.
- You'll be redirected to your **ngrok URL** (not `localhost`).
- Log back into Open Chat Studio if prompted.
- Navigate again to **Team Settings → Messaging Providers** and complete the Slack setup.

## ✅ Done!

Your Slack app is now integrated with your local development environment!
