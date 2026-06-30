# Open Chat Studio
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio) [![codecov](https://codecov.io/github/dimagi/open-chat-studio/graph/badge.svg?token=SUKZMAWM3O)](https://codecov.io/github/dimagi/open-chat-studio)

Open Chat Studio is a platform for building, deploying, and evaluating AI-powered chat applications. It provides tools for working with various LLMs (Large Language Models), creating chatbots, managing conversations, and integrating with different messaging platforms.

[User Documentation](https://docs.openchatstudio.com) | [Developer Documentation](https://developers.openchatstudio.com/)

## Contributing

We welcome contributions to Open Chat Studio! If you're interested in contributing, please check out our [contributing guidelines](https://developers.openchatstudio.com/contributing/) for more information on how to get started.

## Quick Start Setup

Open Chat Studio uses [UV](https://docs.astral.sh/uv/getting-started/installation/) and [Invoke](https://www.pyinvoke.org/) for dev automation.

### Prerequisites

- Python 3.13 (recommended)
- Node.js >= 24.0.0
- Docker and Docker Compose

### Setup

```bash
git clone https://github.com/dimagi/open-chat-studio.git
cd open-chat-studio
uv venv --python 3.13
source .venv/bin/activate
uv sync
inv setup-dev-env   # installs hooks, starts services, migrates DB, builds frontend, creates superuser
./manage.py runserver
```

Run Celery in a separate terminal — required for LLM interactions:

```bash
inv celery
```

For full setup instructions including manual steps, environment configuration, and troubleshooting, see the [Local Development Setup guide](https://developers.openchatstudio.com/getting-started/local-setup/).

## Docker-Only Development Environment

As an alternative to running Django and Celery on the host, you can run the full stack inside Docker — no local Python or Node installation needed.

```bash
cp .env.example .env   # set SECRET_KEY at minimum
docker compose build
docker compose up
```

For the full setup guide, available services, useful commands, and troubleshooting, see the [Docker Development Setup guide](https://developers.openchatstudio.com/getting-started/docker-setup/).

## Deployment

To deploy your own production instance to Heroku:

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/dimagi/open-chat-studio)

## Getting Help

- **Bug reports & feature requests:** [GitHub Issues](https://github.com/dimagi/open-chat-studio/issues)
- **Developer docs:** [developers.openchatstudio.com](https://developers.openchatstudio.com/)
- **User docs:** [docs.openchatstudio.com](https://docs.openchatstudio.com)
