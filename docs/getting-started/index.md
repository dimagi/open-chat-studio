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
