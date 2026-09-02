# Configuration Reference

All configuration is via environment variables. In production, set `DJANGO_SETTINGS_MODULE=config.settings_production`.

## Required

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key. Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. |
| `DATABASE_URL` | PostgreSQL connection URL, e.g. `postgres://user:pass@host:5432/dbname`. Alternatively set individual `DJANGO_DATABASE_*` variables below. |
| `REDIS_URL` | Redis connection URL, e.g. `redis://host:6379`. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list of hostnames the app will serve, e.g. `yourdomain.com,www.yourdomain.com`. |
| `DJANGO_SETTINGS_MODULE` | Must be `config.settings_production` for production. |

## Database (alternative to DATABASE_URL)

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_DATABASE_NAME` | — | Database name |
| `DJANGO_DATABASE_USER` | — | Database user |
| `DJANGO_DATABASE_PASSWORD` | — | Database password |
| `DJANGO_DATABASE_HOST` | `localhost` | Database host |
| `DJANGO_DATABASE_PORT` | `5432` | Database port |

## Connection behaviour

These apply whether the connection comes from `DATABASE_URL` or the variables above.

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_DATABASE_USE_POOL` | `True` | Use a psycopg connection pool. When disabled, `DJANGO_DATABASE_CONN_MAX_AGE` applies instead |
| `DJANGO_DATABASE_POOL_MIN_SIZE` | `2` | Connection pool minimum size |
| `DJANGO_DATABASE_POOL_MAX_SIZE` | `35` | Connection pool maximum size |
| `DJANGO_DATABASE_POOL_TIMEOUT` | `10` | Connection pool timeout (seconds) |
| `DJANGO_DATABASE_CONN_MAX_AGE` | `0` | Persistent connection lifetime, in seconds. Ignored when the pool is enabled |
| `DJANGO_DATABASE_SSLMODE` | `require` (`prefer` when `DEBUG`) | psycopg `sslmode`. AWS RDS Proxy requires TLS |
| `DJANGO_DISABLE_SERVER_SIDE_CURSORS` | `False` | Set to `True` to stop Django using server-side cursors for `QuerySet.iterator()`. Behind a connection proxy in transaction-pooling mode (e.g. AWS RDS Proxy) these are declared `WITH HOLD` and pin the session to a backend connection. Disabling them costs memory: each `iterator()` call then buffers its whole result set client-side |

## Redis (alternative to REDIS_URL)

| Variable | Description |
|----------|-------------|
| `REDIS_HOST` | Redis hostname |
| `REDIS_PORT` | Redis port |
| `REDIS_USE_TLS` | Set to `True` to enable TLS (e.g. for managed Redis with TLS) |

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `CRYPTOGRAPHY_KEY` | `SECRET_KEY` | Encryption key for sensitive fields (API keys, credentials). Set explicitly in production. |
| `CRYPTOGRAPHY_SALT` | — | Additional salt for field encryption. |
| `CSRF_TRUSTED_ORIGINS` | `[]` | Comma-separated list of trusted origins for CSRF, e.g. `https://yourdomain.com`. Required when behind a reverse proxy. |
| `DJANGO_SECURE_SSL_REDIRECT` | `True` | Redirect HTTP to HTTPS. Set to `False` if TLS is terminated upstream and you want to disable the redirect in Django. |
| `OIDC_RSA_PRIVATE_KEY` | — | RSA private key (PEM format) for the built-in OAuth2/OIDC provider. Required if you enable OAuth2 token issuance. |
| `OAUTH_PKCE_REQUIRED` | `True` | Require PKCE for OAuth2 flows. |
| `HEALTH_CHECK_TOKENS` | `[]` | Comma-separated tokens for the `/status` health check endpoint. |

## Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_ENFORCE` | `False` | When `False`, over-limit requests are served and logged as `rate_limit.would_block` (sampled after the first crossing, not every request); when `True`, they receive HTTP 429. The `channels` scope does not answer to this switch: it counts and logs in both states and never returns 429. |
| `RATE_LIMIT_API` | `2000/5m` | Request limit for the `api` scope, format `count/window` with `s`/`m`/`h` units. Fails open: if the limiter's cache is unreachable, requests are served. |
| `RATE_LIMIT_ADMIN_API` | `100/5m` | Request limit for the `admin_api` scope (the `/admin/api/*` autocomplete and provider-reporting endpoints). Keyed by authenticated user, then the provider-reporting token, then client IP, so anonymous traffic cannot spend a staff member's allowance. Set `RATE_LIMIT_TRUSTED_PROXY_COUNT` before enforcing, or all anonymous callers behind a proxy share one bucket. Fails open: if the limiter's cache is unreachable, requests are served. |
| `RATE_LIMIT_CHAT_API` | `300/5m` | Request limit for the `chat_api` scope (the `/api/chat/*` endpoints; the embedded chat widget is the primary caller, but non-widget clients use these endpoints too). Session starts are keyed per widget channel, except public link channels, which are keyed per visitor IP; after start, per session. Set `RATE_LIMIT_TRUSTED_PROXY_COUNT` before enforcing behind a proxy, or those legacy callers all share one bucket. Sits apart from `RATE_LIMIT_API` so one busy conversation cannot spend the team's interactive API allowance. Fails open: if the limiter's cache is unreachable, requests are served. |
| `RATE_LIMIT_PUBLIC_CHAT` | `100/5m` | Request limit for the `public_chat` scope. Applies to the legacy public chat pages and to the public link page `/c/<token>/`, keyed per visitor IP. Set `RATE_LIMIT_TRUSTED_PROXY_COUNT` before enforcing behind a proxy, or every visitor starting a conversation shares one bucket. The poll that runs while a reply is being composed is excluded from the scope, so a slow answer does not spend a conversation's allowance. Over-limit requests receive the site's error page rather than a JSON body, since these views are reached in a browser. Fails open: if the limiter's cache is unreachable, requests are served. |
| `RATE_LIMIT_CHANNELS` | `3000/5m` | Request limit for the `channels` scope (inbound channel deliveries: Telegram, Twilio, Meta Cloud API, Turn, SureAdhere, CommCare Connect, Slack). Keyed per chatbot channel: each delivery is counted inside its view, once it has resolved to a channel and passed the provider's signature check, so a delivery that resolves to no channel is not counted and no caller can spend another tenant's allowance. A Meta payload carrying several phone numbers is counted once per number, against each one's own channel. On Slack this covers the messages the bot answers (mentions, DMs and replies in an existing thread); other channel traffic resolves no chatbot channel and is not counted. This scope counts but never refuses, in both `RATE_LIMIT_ENFORCE` states: an over-limit delivery is logged as `rate_limit.would_block` and still processed, since refusing it would discard a participant's message rather than delay it. Traffic that never resolves to a channel is outside this scope entirely, and is bounded by the WAF rather than here. Fails open: if the limiter's cache is unreachable, deliveries are served. |
| `RATE_LIMIT_CREDENTIALS` | `100/5m` | Request limit for the `credentials` scope (the OAuth client-credential endpoints at `/o/token/`, `/o/revoke_token/` and `/o/introspect/`, API requests whose key or bearer token is rejected, and the CommCare Connect key exchange at `/api/commcare_connect/generate_key`, which issues an outbound request to CommCare Connect before it knows whether the caller's token is valid). Keyed by client IP, because a caller failing authentication has no identity to key on. Set `RATE_LIMIT_TRUSTED_PROXY_COUNT` before enforcing behind a proxy, or every caller shares one bucket. The one scope that fails closed: where the others serve the request when the limiter's cache is unreachable, this one refuses it once enforcement is on, so that a counter nobody can read does not become a way to brute force credentials unobserved. Successful API requests are counted under `RATE_LIMIT_API` instead, so a working integration is never charged to this scope. |
| `RATE_LIMIT_TRUSTED_PROXY_COUNT` | `0` | Number of trusted reverse proxies; required for correct client IPs behind a proxy or tunnel before enabling any IP-keyed scope. |

Per-IP keying reads the client address through `RATE_LIMIT_TRUSTED_PROXY_COUNT`; behind a proxy or load balancer set it, or every visitor shares one bucket.

## Public link host

The public link page and its API access are pinned to the hostname of the Django `Site` row (`Site.objects.get_current().domain`). If that domain is not the deployed host, public links 404 and their chat starts are refused with 403. The value is cached per process, so a change needs a restart.

## Email

One of the following email backends must be configured. Set `DJANGO_EMAIL_BACKEND` to choose:

### Mailgun (default)

```env
DJANGO_EMAIL_BACKEND=anymail.backends.mailgun.EmailBackend
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_SENDER_DOMAIN=mail.yourdomain.com
```

### Amazon SES

```env
DJANGO_EMAIL_BACKEND=anymail.backends.amazon_ses.EmailBackend
# Omit these if using IAM roles:
AWS_SES_ACCESS_KEY=
AWS_SES_SECRET_KEY=
AWS_SES_REGION=us-east-1
```

### Other settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ACCOUNT_EMAIL_VERIFICATION` | `mandatory` | Set to `none` to disable email verification (not recommended for production). |
| `DJANGO_SERVER_EMAIL` | `noreply@dimagi.com` | From address for error emails. |
| `DJANGO_DEFAULT_FROM_EMAIL` | `noreply@dimagi.com` | From address for user-facing emails. |

## File Storage (S3)

Without S3, user-uploaded files are stored on the local filesystem. This is only suitable for single-instance deployments. For multi-instance or Heroku/container deployments, use S3.

| Variable | Description |
|----------|-------------|
| `USE_S3_STORAGE` | Set to `True` to enable S3 for media storage. |
| `AWS_ACCESS_KEY_ID` | AWS access key (omit if using IAM roles). |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key (omit if using IAM roles). |
| `AWS_S3_REGION` | S3 region, e.g. `us-east-1`. May be left blank for S3-compatible providers (a dummy region is used for signing). |
| `AWS_PUBLIC_STORAGE_BUCKET_NAME` | Bucket for public user uploads (e.g. profile images). |
| `AWS_PRIVATE_STORAGE_BUCKET_NAME` | Bucket for private user uploads. |
| `WHATSAPP_S3_AUDIO_BUCKET` | Bucket for WhatsApp voice message audio files. |
| `AWS_S3_ENDPOINT_URL` | Optional. Endpoint for S3-compatible storage, e.g. `https://minio.example.com`. Omit for AWS S3. |
| `AWS_S3_ADDRESSING_STYLE` | Optional. `path` (MinIO, IP endpoints), `virtual` (most cloud providers), or unset (auto). Must match your endpoint. |
| `AWS_S3_CUSTOM_DOMAIN` | Optional. Public-media URL prefix as a browser sees it. Include the bucket for path-style (`host/bucket`); host-only for virtual-host/CDN. Omit for the AWS default. |

### S3-compatible providers

The settings above also work with any S3-compatible service (MinIO, Cloudflare R2, Backblaze B2, Wasabi, DigitalOcean Spaces, etc.). Leave the three `AWS_S3_*` overrides blank for plain AWS. These apply to both media storage and the WhatsApp audio bucket.

MinIO (path-style addressing — note the bucket is included in `AWS_S3_CUSTOM_DOMAIN`):

```env
USE_S3_STORAGE=True
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_ENDPOINT_URL=http://minio:9000
AWS_S3_ADDRESSING_STYLE=path
AWS_S3_CUSTOM_DOMAIN=minio.example.com/public-bucket
AWS_PUBLIC_STORAGE_BUCKET_NAME=public-bucket
AWS_PRIVATE_STORAGE_BUCKET_NAME=private-bucket
WHATSAPP_S3_AUDIO_BUCKET=whatsapp-audio
```

Cloudflare R2 (virtual-host addressing):

```env
USE_S3_STORAGE=True
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
AWS_S3_ADDRESSING_STYLE=virtual
AWS_S3_CUSTOM_DOMAIN=pub.<accountid>.r2.cloudflarestorage.com
AWS_PUBLIC_STORAGE_BUCKET_NAME=public-bucket
AWS_PRIVATE_STORAGE_BUCKET_NAME=private-bucket
```

Notes:

- Private file downloads are served via presigned URLs generated against `AWS_S3_ENDPOINT_URL`, so they work automatically with any provider.
- For path-style endpoints (MinIO), `AWS_S3_CUSTOM_DOMAIN` must include the bucket segment; for virtual-host, it must not.

## Integrations

### Slack

Required only if you want users to connect Slack channels to their chatbots.

| Variable | Description |
|----------|-------------|
| `SLACK_CLIENT_ID` | Slack app client ID |
| `SLACK_CLIENT_SECRET` | Slack app client secret |
| `SLACK_SIGNING_SECRET` | Slack app signing secret |
| `SLACK_BOT_NAME` | Display name for the Slack bot |

### Telegram

| Variable | Description |
|----------|-------------|
| `TELEGRAM_SECRET_TOKEN` | Optional. Secret token for verifying inbound webhook authenticity via the `X-Telegram-Bot-Api-Secret-Token` header. When configured, OCS registers it with Telegram's `setWebhook` API and rejects requests without matching tokens. |

## Observability

| Variable | Description |
|----------|-------------|
| `SENTRY_DSN` | Sentry DSN for error tracking. |
| `SENTRY_ENVIRONMENT` | Sentry environment tag, e.g. `production`. |
| `ENABLE_JSON_LOGGING` | Set to `True` for structured JSON log output (recommended for log aggregation). |

## Task Badger (optional)

[Task Badger](https://taskbadger.net/) provides visibility into Celery task execution.

| Variable | Description |
|----------|-------------|
| `TASKBADGER_ORG` | Task Badger organisation slug |
| `TASKBADGER_PROJECT` | Task Badger project slug |
| `TASKBADGER_API_KEY` | Task Badger API key |

## Analytics

| Variable | Description |
|----------|-------------|
| `GOOGLE_ANALYTICS_ID` | Google Analytics measurement ID |

## Legal / Branding

| Variable | Description |
|----------|-------------|
| `TERMS_URL` | URL to your Terms of Service page (shown in the UI) |
| `PRIVACY_POLICY_URL` | URL to your Privacy Policy page (shown in the UI) |

## Zero Trust Access (optional)

Required only when using [Cloudflare Tunnel](./cloudflare_tunnel.md) as the Zero Trust access layer. Not needed for standard reverse-proxy deployments.

| Variable | Description |
|----------|-------------|
| `CLOUDFLARE_TUNNEL_TOKEN` | Tunnel token from the Cloudflare Zero Trust dashboard. See [Cloudflare Tunnel setup](./cloudflare_tunnel.md) for how to obtain this. |

## System Agent

The System Agent is an internal AI assistant used for certain platform features. Configure the models it can use:

| Variable | Description |
|----------|-------------|
| `SYSTEM_AGENT_MODELS_HIGH` | Model(s) for complex tasks, e.g. `openai:gpt-4o`. Use comma-separated values for fallback. |
| `SYSTEM_AGENT_MODELS_LOW` | Model(s) for simple tasks, e.g. `openai:gpt-4o-mini`. |
| `SYSTEM_AGENT_API_KEYS` | Provider API keys as `provider=key` pairs, e.g. `openai=sk-...,anthropic=sk-ant-...`. |
