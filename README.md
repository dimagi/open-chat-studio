# Open Chat Studio

Experiments with AI, GPT and LLMs. See [this wiki](https://github.com/dimagi/open-chat-studio/wiki) for more information.

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

```
inv up
cp .env.example .env
./manage.py migrate
```

#### Build the front-end resources

To build JavaScript and CSS files, first install npm packages:

```bash
npm install
npm run dev
```

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
celery -A gpt_playground worker -l INFO
```

Or with celery beat (for scheduled tasks):

```bash
celery -A gpt_playground worker -l INFO -B
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
./manage.py test
```

Or to test a specific app/module:

```bash
./manage.py test apps.utils.tests.test_slugs
```

On Linux-based systems you can watch for changes using the following:

```bash
find . -name '*.py' | entr python ./manage.py test apps.utils.tests.test_slugs
```

### Testing bots

To test a bot, first create an experiment. This can be done in the Django admin.

After doing that you can use the UI to create a new chat session against the experiment.

Note that celery needs to be running and your `OPENAI_API_KEY` needs to be set in order to get responses from the bot.

You can also run experiments on the command line using:

```bash
python manage.py run_experiment <experiment_pk>
```

### Notes
#### Signup page
By default the signup page is `disabled`. To enable it, you should set the `SIGNUP_ENABLED` environment variable to `true`

#### Testing webhooks
To test the webhooks, you can use a tool like [ngrok](https://ngrok.com/docs/getting-started/) to forward webhook data to your local machine.

#### Auditing
We use the [django-field-audit](https://github.com/dimagi/django-field-audit) library for auditing. Please see the [table of audited methods](https://github.com/dimagi/django-field-audit#audited-db-write-operations) and familiarize yourself on how to audit "special" functions like `QuerySet.bulk_create()`.