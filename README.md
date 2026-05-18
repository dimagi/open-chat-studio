# Open Chat Studio
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio) [![codecov](https://codecov.io/github/dimagi/open-chat-studio/graph/badge.svg?token=SUKZMAWM3O)](https://codecov.io/github/dimagi/open-chat-studio)

Open Chat Studio is a platform for building, deploying, and evaluating AI-powered chat applications. It provides tools for working with various LLMs (Language Learning Models), creating chatbots, managing conversations, and integrating with different messaging platforms.

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/dimagi/open-chat-studio)

[User Documentation](https://docs.openchatstudio.com)

[Developer Documentation](https://developers.openchatstudio.com/)

## Contributing

We welcome contributions to Open Chat Studio! If you're interested in contributing, please check out our [contributing guidelines](https://developers.openchatstudio.com/contributing/) for more information on how to get started.

## Quick Start Setup

Open Chat Studio uses [UV](https://docs.astral.sh/uv/getting-started/installation/) and [Invoke](https://www.pyinvoke.org/) for dev automation.

### Prerequisites

- Python 3.13 (recommended)
- Node.js >= 24.0.0
- Docker and Docker Compose
- Cloned repo

### Installation Steps

1. **Install dependencies**

    ```bash
    uv venv --python 3.13
    source .venv/bin/activate
    uv sync
    ```

2. **Run the automated setup**

    ```bash
    inv setup-dev-env
    ```

    This will:
    - Install pre-commit hooks
    - Start database and Redis services
    - Run database migrations
    - Build frontend resources
    - Create a superuser
  
   OR
   
    <details>

    <summary>Follow these manual steps</summary>

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
   </detail>

4. **Start the development server**

    ```bash
    ./manage.py runserver
    ```

5. **Run Celery for background tasks**

    Celery is required to handle LLM interactions. Run it using:

    ```bash
    inv celery
    ```

### Docker-Only Development Environment

As an alternative to running Django and Celery on the host, you can run the full stack inside Docker. This requires only Docker — no local Python or Node installation needed.

### Prerequisites

- Docker and Docker Compose

### Setup

1. **Create your `.env` file**

    ```bash
    cp .env.example .env
    ```

    Edit `.env` and set at minimum `SECRET_KEY`. The `DATABASE_URL` and `REDIS_URL` values in `.env` are ignored when running via Docker Compose — those are overridden by the compose file to use the container service names.

2. **Build the images**

    ```bash
    docker compose build
    ```

3. **Start everything**

    ```bash
    docker compose up
    ```

    On first start, Docker Compose will:
    - Start PostgreSQL and wait until it is healthy
    - Start Redis
    - Run `python manage.py migrate` (the `migrate` service exits once complete)
    - Start the Django dev server on [http://localhost:8000](http://localhost:8000)
    - Start a Celery worker and Celery Beat scheduler

4. **Create a superuser**

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
