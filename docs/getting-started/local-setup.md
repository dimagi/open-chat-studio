# Local Development Setup

Open Chat Studio uses [UV](https://docs.astral.sh/uv/getting-started/installation/) and [Invoke](https://www.pyinvoke.org/) for dev automation.

## Prerequisites

- Python 3.13 (recommended)
- Node.js >= 24.0.0
- Docker and Docker Compose
- Git

## Installation Steps

1. **Clone the repository**

    ```bash
    git clone https://github.com/dimagi/open-chat-studio.git
    cd open-chat-studio
    ```

2. **Install dependencies**

    ```bash
    uv venv --python 3.13
    source .venv/bin/activate
    uv sync --locked
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

        ### Install the pre-commit hooks

        ```shell
        prek install --install-hooks
        ```

        ### Set up database

        Start the database and redis services and run the DB migrations:

        ```shell
        inv up  # start the docker services
        cp .env.example .env
        ./manage.py migrate
        ```

        ### Build the front-end resources

        This project uses [pnpm](https://pnpm.io/) (provided via corepack, which ships with Node.js).
        Enable it once with `corepack enable`.

        To build JavaScript and CSS files, first install pnpm packages:

        ```bash
        inv pnpm --install
        # or
        pnpm install
        pnpm run dev
        ```

        **Note**

        You should be using node >= 24.0.0. If you have [nvm](https://github.com/nvm-sh/nvm/blob/master/README.md)
        installed, you can run `nvm use` to switch to the correct version.

        To check which version you are using use `node --version`.

        ### Create a superuser

        ```bash
        ./manage.py createsuperuser
        ```

4. **Start everything**

    ```bash
    inv dev
    ```

    See [Running the dev environment](#running-the-dev-environment) below.

## Running the dev environment

`inv dev` is the command you want for day-to-day work. It runs Django, the Celery worker and the
webpack asset watcher together in one terminal (via [honcho](https://github.com/nickstenning/honcho)
and `Procfile.dev`), and all three restart on code changes:

```bash
inv dev
```

| Process | What it does |
|---|---|
| `web` | Django dev server |
| `worker` | Celery worker — **required** for LLM interactions and other background tasks |
| `assets` | webpack watcher for JavaScript and CSS |

### Named URLs with portless

If [portless](https://www.npmjs.com/package/portless) is installed and its proxy is running, the
Django server is exposed on a stable named URL — `https://ocs.localhost` — instead of a port
number. Started on a non-privileged port (`portless proxy start -p 1355`) the URL becomes
`http://ocs.localhost:1355`.

Names are allocated per running server, so a second worktree does not collide: if `ocs` is already
taken, the next one becomes `ocs1`, then `ocs2`, and so on. Run `portless list` to see which name
maps to which worktree.

Without portless, Django falls back to `http://127.0.0.1:8000` as usual.

### Running services individually

`inv dev` is a convenience wrapper — each process can also be run on its own:

```bash
inv runserver   # alias: inv django
inv celery
pnpm run dev-watch
```

`inv celery` consumes all [task queues](../hosting/index.md#task-queues). For a production-like
setup use `inv celery --threads`. To reproduce the production split — where chat, background and
evaluation work get separate workers — run one process per queue:

```bash
inv celery --queues=celery
inv celery --queues=background
inv celery --queues=evaluations
```

---

Next: [Development Workflow](dev-workflow.md)
