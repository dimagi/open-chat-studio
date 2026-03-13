# Docker Compose Deployment

This guide covers deploying Open Chat Studio on a single server or small cluster using `docker-compose.prod.yml`.

!!! note "For larger deployments"
    For container-orchestrated deployments (ECS, Kubernetes), see the [AWS Fargate](./aws.md) guide instead.
    For managed PaaS hosting, see the [Heroku](./heroku.md) guide.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- A domain name with DNS pointing to your server
- A reverse proxy (nginx, Caddy, Traefik) handling TLS termination

## Step 1: Build the Image

Clone the repository and build the production image.

```bash
git clone https://github.com/dimagi/open-chat-studio.git
cd open-chat-studio

docker build -t open-chat-studio:latest .
```

## Step 2: Create the Environment File

Copy the example and fill in your values:

```bash
cp .env.example .env.prod
```

At minimum, set:

```bash
# .env.prod

SECRET_KEY=<generate a strong secret key>
DJANGO_SETTINGS_MODULE=config.settings_production
DJANGO_ALLOWED_HOSTS=yourdomain.com

# If using the bundled PostgreSQL container:
DATABASE_URL=postgres://postgres:yourpassword@db:5432/open_chat_studio

# If using the bundled Redis container:
REDIS_URL=redis://redis:6379

# Email (required for user registration)
DJANGO_EMAIL_BACKEND=anymail.backends.mailgun.EmailBackend
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_SENDER_DOMAIN=mail.yourdomain.com

# Encryption (recommended: set explicitly rather than relying on SECRET_KEY)
CRYPTOGRAPHY_KEY=<generate a strong key>
CRYPTOGRAPHY_SALT=<generate a random salt>
```

See [Configuration Reference](./configuration.md) for all available options.

## Step 3: Start the Services

If using the bundled PostgreSQL container, also set `POSTGRES_PASSWORD` (it must match the password in `DATABASE_URL`):

```bash
POSTGRES_PASSWORD=yourpassword docker compose -f docker-compose.prod.yml up -d
```

Or export it first:

```bash
export POSTGRES_PASSWORD=yourpassword
docker compose -f docker-compose.prod.yml up -d
```

On first start, the `migrate` service runs all database migrations and then exits before the web and worker services start.

## Step 4: Create a Superuser

```bash
docker compose -f docker-compose.prod.yml run --rm web python manage.py createsuperuser
```

Then log in at `https://yourdomain.com/admin/` and create a Team.

## Step 5: Configure a Reverse Proxy

The `web` service listens on port `8000` (or `$PORT`). Put a TLS-terminating reverse proxy in front of it.

### Example: Caddy

```caddyfile
yourdomain.com {
    reverse_proxy localhost:8000
}
```

### Example: nginx

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    # ... TLS config ...

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Also add `CSRF_TRUSTED_ORIGINS=https://yourdomain.com` to `.env.prod`.

## Useful Commands

```bash
# View logs
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml logs -f celery_worker

# Run a management command
docker compose -f docker-compose.prod.yml run --rm web python manage.py <command>

# Apply migrations after an upgrade
docker compose -f docker-compose.prod.yml run --rm migrate

# Rebuild after a code update
docker build -t open-chat-studio:latest .
docker compose -f docker-compose.prod.yml up -d
```

## Scaling

To run multiple web workers, either increase `WEB_WORKERS` (threads within one container):

```bash
WEB_WORKERS=4 docker compose -f docker-compose.prod.yml up -d
```

Or scale the web service to multiple containers (requires an external load balancer and shared storage/S3):

```bash
docker compose -f docker-compose.prod.yml up -d --scale web=3
```

!!! warning
    Run **exactly one** `celery_beat` container. Running multiple instances will cause duplicate scheduled tasks.

## Using Managed Database and Redis

For production resilience, replace the bundled `db` and `redis` containers with managed services. Update your `.env.prod`:

```bash
DATABASE_URL=postgres://user:pass@your-rds-endpoint:5432/open_chat_studio
REDIS_URL=rediss://your-elasticache-endpoint:6379  # note: rediss:// for TLS
REDIS_USE_TLS=True
```

Then remove the `db` and `redis` services from your compose file (or use an override file).

!!! warning "pgvector requirement"
    Your managed PostgreSQL instance must have the `pgvector` extension enabled (version ≥ 0.7.0). On Amazon RDS this is available from PostgreSQL 15.2+. On Google Cloud SQL it is available from PostgreSQL 14+.
