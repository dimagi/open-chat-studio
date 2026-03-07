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
| `DJANGO_DATABASE_POOL_MIN_SIZE` | — | Connection pool minimum size |
| `DJANGO_DATABASE_POOL_MAX_SIZE` | — | Connection pool maximum size |
| `DJANGO_DATABASE_POOL_TIMEOUT` | — | Connection pool timeout (seconds) |

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

## Email

One of the following email backends must be configured. Set `DJANGO_EMAIL_BACKEND` to choose:

### Mailgun (default)

```
DJANGO_EMAIL_BACKEND=anymail.backends.mailgun.EmailBackend
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_SENDER_DOMAIN=mail.yourdomain.com
```

### Amazon SES

```
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
| `AWS_S3_REGION` | S3 region, e.g. `us-east-1`. |
| `AWS_PUBLIC_STORAGE_BUCKET_NAME` | Bucket for public user uploads (e.g. profile images). |
| `AWS_PRIVATE_STORAGE_BUCKET_NAME` | Bucket for private user uploads. |
| `WHATSAPP_S3_AUDIO_BUCKET` | Bucket for WhatsApp voice message audio files. |

## Integrations

### Slack

Required only if you want users to connect Slack channels to their chatbots.

| Variable | Description |
|----------|-------------|
| `SLACK_CLIENT_ID` | Slack app client ID |
| `SLACK_CLIENT_SECRET` | Slack app client secret |
| `SLACK_SIGNING_SECRET` | Slack app signing secret |
| `SLACK_BOT_NAME` | Display name for the Slack bot |

### Observability

| Variable | Description |
|----------|-------------|
| `SENTRY_DSN` | Sentry DSN for error tracking. |
| `SENTRY_ENVIRONMENT` | Sentry environment tag, e.g. `production`. |
| `ENABLE_JSON_LOGGING` | Set to `True` for structured JSON log output (recommended for log aggregation). |

### Task Badger (optional)

[Task Badger](https://taskbadger.net/) provides visibility into Celery task execution.

| Variable | Description |
|----------|-------------|
| `TASKBADGER_ORG` | Task Badger organisation slug |
| `TASKBADGER_PROJECT` | Task Badger project slug |
| `TASKBADGER_API_KEY` | Task Badger API key |

### Analytics

| Variable | Description |
|----------|-------------|
| `GOOGLE_ANALYTICS_ID` | Google Analytics measurement ID |

### Legal / Branding

| Variable | Description |
|----------|-------------|
| `TERMS_URL` | URL to your Terms of Service page (shown in the UI) |
| `PRIVACY_POLICY_URL` | URL to your Privacy Policy page (shown in the UI) |

## System Agent

The System Agent is an internal AI assistant used for certain platform features. Configure the models it can use:

| Variable | Description |
|----------|-------------|
| `SYSTEM_AGENT_MODELS_HIGH` | Model(s) for complex tasks, e.g. `openai:gpt-4o`. Use comma-separated values for fallback. |
| `SYSTEM_AGENT_MODELS_LOW` | Model(s) for simple tasks, e.g. `openai:gpt-4o-mini`. |
| `SYSTEM_AGENT_API_KEYS` | Provider API keys as `provider=key` pairs, e.g. `openai=sk-...,anthropic=sk-ant-...`. |
