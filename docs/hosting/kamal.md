# Kamal Deployment

[Kamal](https://kamal-deploy.org/) is a deployment tool that deploys Docker containers to any server via SSH — no special cloud infrastructure required. It handles zero-downtime deploys, SSL via its built-in `kamal-proxy`, and rolling updates.

!!! note "For other deployment options"
    For Docker Compose on a single server, see the [Docker Compose](./docker.md) guide.
    For managed PaaS hosting, see the [Heroku](./heroku.md) guide.

## Prerequisites

- One or more servers running Ubuntu 22.04+ (or compatible Linux)
- SSH access to your server(s) as a non-root user with sudo privileges
- A domain name with DNS pointing to your server
- Docker installed on your local machine (Kamal installs Docker on remote servers automatically)
- Ruby installed locally (for the Kamal gem), or use the Kamal Docker image

## Install Kamal

```bash
gem install kamal
```

Or use the Docker wrapper without installing Ruby:

```bash
alias kamal='docker run -it --rm -v "${PWD}:/workdir" -v "${SSH_AUTH_SOCK}:/ssh-agent" -e "SSH_AUTH_SOCK=/ssh-agent" -v /var/run/docker.sock:/var/run/docker.sock ghcr.io/basecamp/kamal:latest'
```

## Configuration

Create a `config/deploy.yml` at the root of your project:

```yaml
service: open-chat-studio
image: your-registry/open-chat-studio

servers:
  web:
    hosts:
      - your-server-ip
    options:
      expose: "8000"
  workers:
    hosts:
      - your-server-ip
    cmd: celery -A config worker -l INFO --pool gevent --concurrency 100
  beat:
    hosts:
      - your-server-ip
    cmd: celery -A config beat -l INFO

ssh:
  user: deploy  # non-root user with sudo privileges

builder:
  arch: amd64
  driver: docker  # avoids https://github.com/docker/buildx/issues/1519

registry:
  server: your-registry-server
  username: your-registry-username
  password:
    - KAMAL_REGISTRY_PASSWORD

env:
  clear:
    DJANGO_SETTINGS_MODULE: config.settings_production
    DJANGO_ALLOWED_HOSTS: yourdomain.com
    CSRF_TRUSTED_ORIGINS: https://yourdomain.com
  secret:
    - SECRET_KEY
    - DATABASE_URL
    - REDIS_URL
    - CRYPTOGRAPHY_KEY
    - CRYPTOGRAPHY_SALT
    - MAILGUN_API_KEY

accessories:
  postgres:
    image: pgvector/pgvector:pg16
    host: your-server-ip
    directories:
      - data:/var/lib/postgresql/data
    env:
      clear:
        POSTGRES_USER: open_chat_studio
        POSTGRES_DB: open_chat_studio
      secret:
        - POSTGRES_PASSWORD

  redis:
    image: redis:7
    host: your-server-ip
    directories:
      - data:/data

proxy:
  ssl: true
  host: yourdomain.com
  app_port: 8000

logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

### Secret management

Store secrets in a `.kamal/secrets` file (do not commit this to git):

```bash
SECRET_KEY=your-secret-key
DATABASE_URL=postgres://open_chat_studio:yourpassword@open-chat-studio-postgres:5432/open_chat_studio
REDIS_URL=redis://open-chat-studio-redis:6379
CRYPTOGRAPHY_KEY=your-cryptography-key
CRYPTOGRAPHY_SALT=your-cryptography-salt
MAILGUN_API_KEY=your-mailgun-api-key
POSTGRES_PASSWORD=yourpassword
KAMAL_REGISTRY_PASSWORD=your-registry-password
```

Add `.kamal/secrets` to your `.gitignore`.

!!! note "Accessory hostnames"
    Kamal accessories are reachable from your app containers using the hostname `<service>-<accessory>` — e.g. `open-chat-studio-postgres` and `open-chat-studio-redis`. Use these in your `DATABASE_URL` and `REDIS_URL`.

See [Configuration Reference](./configuration.md) for all available environment variables.

## Add a Database Migration Hook

Kamal supports deploy hooks. Create `.kamal/hooks/pre-deploy` to run migrations before each deploy:

```bash
#!/bin/bash
set -e
kamal app exec --reuse 'python manage.py migrate --noinput'
```

Make it executable:

```bash
chmod +x .kamal/hooks/pre-deploy
```

## First Deploy

```bash
# Bootstrap servers (installs Docker, sets up kamal-proxy, starts accessories)
kamal setup

# Create a superuser after the first deploy
kamal app exec -i 'python manage.py createsuperuser'
```

Then log in at `https://yourdomain.com/admin/` and create a Team.

## Subsequent Deploys

```bash
kamal deploy
```

This builds a new image, pushes it to your registry, and performs a rolling restart with zero downtime.

## Useful Commands

```bash
# View logs
kamal app logs
kamal app logs --roles workers

# Open a console
kamal app exec -i 'python manage.py shell'

# Run a management command
kamal app exec 'python manage.py <command>'

# Check container status
kamal app details

# Restart a specific role
kamal app restart --roles beat

# Accessory management
kamal accessory boot postgres
kamal accessory logs redis
```

## Multi-server Setup

To spread processes across multiple servers, list additional hosts under each role:

```yaml
servers:
  web:
    hosts:
      - 10.0.0.1
      - 10.0.0.2
  workers:
    hosts:
      - 10.0.0.3
```

!!! warning
    Run the `beat` role on **exactly one** host. Multiple instances will cause duplicate scheduled tasks.
