# Heroku Deployment

Open Chat Studio ships with a `heroku.yml` manifest for deployment as a Docker-based Heroku app.

## Prerequisites

- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) installed and authenticated
- A Heroku account with billing enabled (the PostgreSQL and Redis add-ons require a verified account)

## Step 1: Create the App

```bash
heroku create your-app-name
heroku stack:set container -a your-app-name
```

## Step 2: Provision Add-ons

The `heroku.yml` manifest specifies the required add-ons, but you can also provision them manually:

```bash
# PostgreSQL with pgvector support (requires Standard tier or above for pgvector)
heroku addons:create heroku-postgresql:standard-0 -a your-app-name

# Redis
heroku addons:create heroku-redis:mini -a your-app-name
```

!!! warning "pgvector on Heroku"
    pgvector is only available on Heroku Postgres **Standard** tier and above (not the free/mini tier).
    Ensure your plan supports the `vector` extension before running migrations.

    After provisioning, enable the extension:
    ```bash
    heroku pg:psql -a your-app-name -c "CREATE EXTENSION IF NOT EXISTS vector;"
    ```

## Step 3: Set Config Vars

```bash
heroku config:set -a your-app-name \
  DJANGO_SETTINGS_MODULE=config.settings_production \
  SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')" \
  DJANGO_ALLOWED_HOSTS=your-app-name.herokuapp.com \
  CRYPTOGRAPHY_KEY="$(openssl rand -hex 32)" \
  CRYPTOGRAPHY_SALT="$(openssl rand -hex 16)"
```

Set your email provider (example for Mailgun):

```bash
heroku config:set -a your-app-name \
  DJANGO_EMAIL_BACKEND=anymail.backends.mailgun.EmailBackend \
  MAILGUN_API_KEY=your-key \
  MAILGUN_SENDER_DOMAIN=mail.yourdomain.com
```

For S3 media storage (recommended on Heroku, since the filesystem is ephemeral):

```bash
heroku config:set -a your-app-name \
  USE_S3_STORAGE=True \
  AWS_ACCESS_KEY_ID=... \
  AWS_SECRET_ACCESS_KEY=... \
  AWS_S3_REGION=us-east-1 \
  AWS_PUBLIC_STORAGE_BUCKET_NAME=your-public-bucket \
  AWS_PRIVATE_STORAGE_BUCKET_NAME=your-private-bucket
```

See [Configuration Reference](./configuration.md) for all available variables.

## Step 4: Deploy

```bash
git push heroku main
```

The `heroku.yml` release phase runs `python manage.py migrate` automatically before the new processes start.

## Step 5: Create a Superuser

```bash
heroku run python manage.py createsuperuser -a your-app-name
```

Then visit `https://your-app-name.herokuapp.com/admin/` and create a Team.

## Process Types

The `heroku.yml` defines three process types:

| Type | Command | Scale |
|------|---------|-------|
| `web` | `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 0 config.wsgi:application` | Scale as needed |
| `worker` | `celery -A config worker -l INFO --pool gevent --concurrency 100` | Scale as needed |
| `beat` | `celery -A config beat -l INFO` | **Always exactly 1** |

Scale dynos:

```bash
heroku ps:scale web=1 worker=1 beat=1 -a your-app-name
```

!!! warning
    Always run exactly **one** `beat` dyno. Running more than one will cause duplicate scheduled tasks.

## Custom Domain

```bash
heroku domains:add yourdomain.com -a your-app-name
heroku config:set DJANGO_ALLOWED_HOSTS=yourdomain.com,your-app-name.herokuapp.com -a your-app-name
heroku config:set CSRF_TRUSTED_ORIGINS=https://yourdomain.com -a your-app-name
```

Heroku will provision a TLS certificate automatically via ACM when you add a custom domain on a paid dyno.

## Upgrading

```bash
git pull origin main
git push heroku main
```

Migrations run automatically in the release phase. Monitor the release log:

```bash
heroku releases -a your-app-name
heroku logs --tail -a your-app-name
```
