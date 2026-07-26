# Meta Cloud API (WhatsApp) Integration

This guide explains the configuration parameters required to set up a Meta Cloud API messaging provider for WhatsApp, and how each parameter is used throughout the system.

## Provider Configuration

When creating a Meta Cloud API messaging provider, four parameters are required:

| Parameter | Label | Where it's used |
|-----------|-------|----------------|
| `business_id` | WhatsApp Business Account ID | Phone number validation during channel creation |
| `access_token` | System User Access Token | All Meta Graph API calls |
| `app_secret` | App Secret | Verifying incoming webhook payload signatures |
| `verify_token` | Webhook Verify Token | Meta's one-time webhook URL verification handshake |

All parameters except `business_id` are stored as encrypted fields and obfuscated in the UI.

## Parameter Details

### `verify_token` — Webhook Verification

When you configure a webhook URL in the Meta App Dashboard, Meta sends a **GET request** to verify that you own the endpoint. This request includes the `verify_token` you configured in Meta's dashboard as a query parameter (`hub.verify_token`).

Our system matches this token by comparing a SHA-256 hash of the incoming token against a stored hash in the `MessagingProvider.extra_data` field. This avoids storing the raw token in a queryable column while still allowing efficient database lookups. If the hash matches, we respond with the `hub.challenge` value and Meta considers the webhook verified.

**When it's used:** Once, during the initial webhook setup in the Meta App Dashboard.


### `app_secret` — Payload Signature Verification

Every incoming POST webhook from Meta includes an `X-Hub-Signature-256` header containing an HMAC-SHA256 signature of the request body, signed with your app secret. We verify this signature on every incoming message to ensure the payload genuinely came from Meta and hasn't been tampered with.

**When it's used:** On every incoming webhook POST request, after the channel is looked up (since the `app_secret` is stored in the channel's messaging provider config).

### `business_id` — Phone Number Validation

The `business_id` is your WhatsApp Business Account ID. During channel creation, when a user enters a phone number, we call the Meta Graph API's Phone Number Management endpoint to list all phone numbers registered under this business account:

```
GET https://graph.facebook.com/v25.0/{business_id}/phone_numbers
```

If a match is found, we store the corresponding `phone_number_id` in the channel's `extra_data`, which is then used in API calls when sending outbound messages. If no match is found, the form validation fails.

**When it's used:** During channel creation (form validation in `WhatsappChannelForm.clean_number`).

### `access_token` — API Authentication

The `access_token` is a System User Access Token from your Meta Business account. It is included as a Bearer token in the `Authorization` header for all outgoing Meta Graph API calls, including:

- **Phone number validation** — listing phone numbers under the business account (see `business_id` above)
- **Sending messages** — posting text messages to the WhatsApp Cloud API via `POST https://graph.facebook.com/v25.0/{phone_number_id}/messages`

**When it's used:** Every outgoing API call to Meta.

## How `phone_number_id` Ties It Together

Meta's Cloud API identifies phone numbers by an internal `phone_number_id` rather than the phone number itself. This ID is:

1. **Resolved at channel creation** — looked up via the `business_id` and `access_token`
2. **Stored in `ExperimentChannel.extra_data`** — persisted so it doesn't need to be re-fetched
3. **Used to route incoming webhooks** — the webhook payload includes the `phone_number_id` in `metadata`, which we match against the stored value to find the correct channel
4. **Used as the `from` identifier when sending messages** — the send endpoint is `/{phone_number_id}/messages`

This differs from other WhatsApp providers (Twilio, Turn.io) which use the phone number directly as the identifier. The `WhatsappChannel.from_identifier` property abstracts this difference.

## Webhook Architecture

Unlike Turn.io (which uses per-experiment webhook URLs), the Meta Cloud API uses a **single global webhook endpoint**:

```
/channels/whatsapp/meta/incoming_message
```

This endpoint handles both:

- **GET** requests for webhook verification (using `verify_token`)
- **POST** requests for incoming messages (verified with `app_secret`, routed by `phone_number_id`)

All Meta Cloud API channels share this endpoint. Routing to the correct channel happens by matching the `phone_number_id` from the incoming payload against `ExperimentChannel.extra_data`.
