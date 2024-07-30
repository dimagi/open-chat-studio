# Open Chat Studio

Experiments with AI, GPT and LLMs. See [this wiki](https://dimagi.atlassian.net/wiki/spaces/OCS/overview) for more information.

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/dimagi/open-chat-studio)

## Dev Environment Setup

This project uses [Invoke](https://www.pyinvoke.org/) for dev automation. You can view the list of
available commands with:

```shell
inv -l
```

New commands / updates can be made to the `tasks.py` file.

### 1. Install dependencies
Setup a virtualenv and install requirements:

```bash
python -m venv venv
pip install -r dev-requirements.txt
```

Python 3.11 is recommended.

### 2. Run the automated setup

```shell
inv setup-dev-env
```

This will:

#### Install the pre-commit hooks

```shell
pre-commit install --install-hooks
```

#### Set up database

Start the database and redis services and run the DB migrations:

```shell
docker compose -f docker-compose-dev.yml up -d  # equivalent of `inv up`
cp .env.example .env
./manage.py migrate
```

#### Build the front-end resources

To build JavaScript and CSS files, first install npm packages:

```bash
npm install
npm run dev
```

**Note**

You should be using node >= 18.0.0. If you have [nvm](https://github.com/nvm-sh/nvm/blob/master/README.md) 
installed, you can run `nvm use` to switch to the correct version.

To check which version you are using use `node --version`.

#### Create a superuser

```bash
./manage.py createsuperuser
```

### Running server

```bash
./manage.py runserver
```

## Running Celery

Celery can be used to run background tasks.

**Note:** Celery is required to run in order to get a response from an LLM, so you'll need to run this if you want to test end-to-end conversations.

You can run it using:

```bash
inv celery
# or
celery -A gpt_playground worker -l INFO -B --pool=solo
```

To run a celery process more similar to production, you can use the following command:

```bash
inv celery --gevent
# or
celery -A gpt_playground worker -l INFO -B --pool gevent --concurrency 10
```

## Updating translations

```bash
inv translations
```

## Updating requirements

```shell
inv requirements

Options:
  -p STRING, --upgrade-package=STRING
  -u, --upgrade-all
```

## Installing Git commit hooks

To install the Git commit hooks run the following:

```shell
$ pre-commit install --install-hooks
```

Once these are installed they will be run on every commit.

## Running Tests

To run tests:

```bash
pytest
```

Or to test a specific app/module:

```bash
pytest apps/utils/tests/test_slugs.py
```

### Notes
#### Signup page
By default the signup page is `disabled`. To enable it, you should set the `SIGNUP_ENABLED` environment variable to `true`

#### Testing webhooks
To test the webhooks, you can use a tool like [ngrok](https://ngrok.com/docs/getting-started/) to forward webhook data to your local machine.

#### Auditing
We use the [django-field-audit](https://github.com/dimagi/django-field-audit) library for auditing. Please see the [table of audited methods](https://github.com/dimagi/django-field-audit#audited-db-write-operations) and familiarize yourself on how to audit "special" functions like `QuerySet.bulk_create()`.

#### Linting

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting. You can run it directly or with the `inv ruff`
command:

```
Usage: inv ruff [--options]

Docstring:
  Run ruff checks and formatting. Use --unsafe-fixes to apply unsafe fixes.

Options:
  -n, --no-fix
  -u, --unsafe-fixes
```
