---
hide:
  - navigation
---
# Getting Started

This section will help you set up your development environment and get started with Open Chat Studio.

## Development Environment Setup

Open Chat Studio uses [UV](https://docs.astral.sh/uv/getting-started/installation/) and [Invoke](https://www.pyinvoke.org/) for dev automation.

### Prerequisites

- Python 3.13 (recommended)
- Node.js >= 24.0.0
- Docker and Docker Compose
- Git

### Installation Steps

1. **Clone the repository**

    ```bash
    git clone https://github.com/dimagi/open-chat-studio.git
    cd open-chat-studio
    ```

2. **Install dependencies**

    ```bash
    uv venv --python 3.13
    source .venv/bin/activate
    uv sync
    ```

3. **Run the automated setup**

    ```bash
    inv setup-dev-env
    ```

    This will:
    - Install pre-commit hooks
    - Start database and Redis services
    - Run database migrations
    - Build frontend resources
    - Create a superuser

    ??? note "Manual steps"

        #### Install the pre-commit hooks

        ```shell
        prek install --install-hooks
        ```

        #### Set up database

        Start the database and redis services and run the DB migrations:

        ```shell
        inv up  # start the docker services
        cp .env.example .env
        ./manage.py migrate
        ```

        #### Build the front-end resources

        To build JavaScript and CSS files, first install npm packages:

        ```bash
        inv npm --install
        # or
        npm install
        npm run dev
        ```

        **Note**

        You should be using node >= 24.0.0. If you have [nvm](https://github.com/nvm-sh/nvm/blob/master/README.md)
        installed, you can run `nvm use` to switch to the correct version.

        To check which version you are using use `node --version`.

        #### Create a superuser

        ```bash
        ./manage.py createsuperuser
        ```

4. **Start the development server**

    ```bash
    ./manage.py runserver
    ```

5. **Run Celery for background tasks**

    Celery is required to handle LLM interactions. Run it using:

    ```bash
    inv celery
    ```

    For a production-like setup, use:

    ```bash
    inv celery --gevent
    ```

## Docker-Only Development Environment

As an alternative to running Django and Celery on the host, you can run the full stack inside Docker. This requires only Docker — no local Python or Node installation needed.

### Prerequisites

- Docker and Docker Compose

### Setup

1. **Clone the repository**

    ```bash
    git clone https://github.com/dimagi/open-chat-studio.git
    cd open-chat-studio
    ```

2. **Create your `.env` file**

    ```bash
    cp .env.example .env
    ```

    Edit `.env` and set at minimum `SECRET_KEY`. The `DATABASE_URL` and `REDIS_URL` values in `.env` are ignored when running via Docker Compose — those are overridden by the compose file to use the container service names.

3. **Build the images**

    ```bash
    docker compose build
    ```

4. **Start everything**

    ```bash
    docker compose up
    ```

    On first start, Docker Compose will:
    - Start PostgreSQL and wait until it is healthy
    - Start Redis
    - Run `python manage.py migrate` (the `migrate` service exits once complete)
    - Start the Django dev server on [http://localhost:8000](http://localhost:8000)
    - Start a Celery worker and Celery Beat scheduler

5. **Create a superuser**

    In a separate terminal:

    ```bash
    docker compose run --rm web python manage.py createsuperuser
    ```

### Services

| Service | Description |
|---|---|
| `db` | PostgreSQL with pgvector extension |
| `redis` | Redis (used as Celery broker and result backend) |
| `migrate` | Runs `manage.py migrate` on startup, then exits |
| `web` | Django dev server with auto-reload (`runserver`) |
| `celery_worker` | Celery worker for background tasks |
| `celery_beat` | Celery Beat scheduler (uses `django_celery_beat` database scheduler) |

### Useful commands

Run a management command:

```bash
docker compose run --rm web python manage.py <command>
```

View logs for a specific service:

```bash
docker compose logs -f web
docker compose logs -f celery_worker
```

Rebuild after dependency changes (`pyproject.toml` / `uv.lock`):

```bash
docker compose build
docker compose up
```

Stop all services and remove containers:

```bash
docker compose down
```

### Troubleshooting

#### `type "halfvec" does not exist` during migrations

This error means the `pgvector` extension in your PostgreSQL container is too old. The `halfvec` type requires pgvector ≥ 0.7.0.

**Cause:** Your locally cached `pgvector/pgvector:pg16` Docker image predates pgvector 0.7.0.

**Fix (fresh setup — no data to keep):**

```bash
docker compose down -v      # removes containers and volumes
docker compose pull db       # fetch the latest pgvector image
docker compose up            # reinitialise and re-run migrations
```

**Fix (existing data to preserve):**

```bash
docker compose pull db
docker compose up -d --force-recreate db
docker compose exec db psql -U postgres -d open_chat_studio -c "ALTER EXTENSION vector UPDATE;"
docker compose run --rm migrate
```

#### Database `open_chat_studio` does not exist

PostgreSQL only creates the database named in `POSTGRES_DB` when initialising a **fresh** data volume. If a `postgres_data` volume already exists from a previous run without the database, you will see this error.

**Fix:**

```bash
docker compose exec db createdb -U postgres open_chat_studio
docker compose run --rm migrate
```

Or to start completely fresh (deletes all data):

```bash
docker compose down -v
docker compose up
```

## Common Development Tasks

### Running Tests

```bash
pytest
```

Or to test a specific app/module:

```bash
pytest apps/utils/tests/test_slugs.py
```

### Updating Translations

```bash
inv translations
```

### Linting and Formatting

The project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
inv ruff
```

### Updating Requirements

```bash
inv requirements
```

To add a new requirement:

```bash
uv add <package-name>

# for dev / prod dependencies
uv add <package-name> --group [dev|prod]
```
