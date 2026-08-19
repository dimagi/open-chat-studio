# Turn.io Integration

This guide covers the configuration parameters for a Turn.io messaging provider and how inbound webhooks are authenticated.

## Provider Configuration

| Parameter | Label | Required | Where it's used |
|-----------|-------|----------|----------------|
| `auth_token` | Auth Token | Yes | All outbound calls to the Turn API |
| `hmac_secret` | Webhook HMAC Secret | No | Verifying incoming webhook payload signatures |

Both are stored as encrypted fields and obfuscated in the UI.

## `hmac_secret` — Webhook Signature Verification

Turn signs every webhook delivery with an HMAC-SHA256 of the raw request body, keyed on the
secret configured for that webhook in your Turn account, and sends the base64-encoded digest
in the `X-Turn-Hook-Signature` header.

When `hmac_secret` is set on the provider, OCS recomputes that digest over the raw body and
compares it in constant time. A request whose signature is missing or does not match is
rejected with a `401`, and no message is queued.

When `hmac_secret` is blank, the signature is not checked and the webhook is accepted as
before. This is deliberate: the secret can only be read from the customer's own Turn account,
so requiring it on the day the feature deploys would drop live traffic for every provider
that had not yet copied it across, including self-hosted OCS instances. See
[issue #2346](https://github.com/dimagi/open-chat-studio/issues/2346).

**Leaving it blank means the webhook endpoint is unauthenticated.** Anyone who knows the URL
can forge messages as any participant. Set the secret as soon as your Turn account is
configured to sign.

### Setting it up

1. In your Turn account, open the webhook configuration for the number connected to this chatbot.
2. Copy the HMAC secret shown there. If the webhook has no secret yet, generate one in Turn first.
3. In OCS, open **Team Settings**, then in the **Messaging Providers** section edit the Turn.io provider, paste the value into
   **Webhook HMAC Secret**, and save.
4. Send a test message through WhatsApp and confirm the bot replies.

Verification takes effect on the very next request, so do step 4 immediately. Turn retries a
failed delivery up to five times with incremental backoff, but a `4xx` cancels the retries, so
a mismatched secret loses messages rather than delaying them.

### If messages stop arriving

Clear the **Webhook HMAC Secret** field and save. Delivery resumes immediately, and you can
re-check the value in your Turn account without losing further messages. A `401` in your Turn
dashboard's delivery log confirms the secret did not match what Turn is signing with.

### Verification order

Turn also delivers status callbacks and outbound-message echoes to the same URL. OCS filters
those out before checking the signature, so they continue to receive a `200` rather than
appearing as authentication failures in the Turn dashboard. Only payloads that would be
dispatched to a chatbot are verified.
