# Testing Twilio Integration Locally

This guide covers setting up Twilio WhatsApp integration for local development.

## Prerequisites

- Twilio account with a phone number (or Twilio Sandbox for WhatsApp)
- ngrok installed ([https://ngrok.com/download](https://ngrok.com/download))

## 1. Expose Local Server with ngrok

Start your Django server with a public URL:

```bash
invoke runserver --public
```

This generates a public HTTPS URL like `https://abc123.ngrok.io`.

## 2. Create Messaging Provider

1. Go to **Team Settings → Messaging Providers**
2. Click **Add** and select **Twilio** as the provider type
3. Enter your Twilio Account SID and Auth Token

## 3. Create Channel on Bot

1. Navigate to your bot/experiment
2. Go to **Channels** and add a new WhatsApp channel
3. Select your Twilio messaging provider
4. Note the webhook URL displayed

## 4. Configure Twilio Webhook

1. Go to [Twilio Console](https://console.twilio.com/)
2. Navigate to your phone number settings (Messaging -> Senders -> WhatsApp senders)
3. Under **Messaging Endpoint Configuration**, set the webhook URL:
   - URL format: `https://<your-ngrok-subdomain>.ngrok.io/channels/twilio/<channel-id>/incoming/`
4. Set the HTTP method to **POST**

**Important:** The callback URL must use your ngrok domain, not localhost.

## Troubleshooting

- **Webhook not receiving messages:** Verify ngrok is running and the URL matches exactly
- **Signature validation errors:** Ensure the Auth Token in OCS matches your Twilio account and that Django is using HTTPS for generating abosulte URLs.
- **Voice transcription failing:** Check voice provider credentials and that the provider supports transcription
