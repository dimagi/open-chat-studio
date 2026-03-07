FROM python:3.13-slim-bullseye AS build-python
RUN apt-get update \
  # dependencies for building Python packages
  && apt-get install -y build-essential libpq-dev

# This approximately follows this guide: https://hynek.me/articles/docker-uv/
# Which creates a standalone environment with the dependencies.
# - Silence uv complaining about not being able to use hard links,
# - tell uv to byte-compile packages for faster application startups,
# - prevent uv from accidentally downloading isolated Python builds,
# - pick a Python (use `/usr/bin/python3.12` on uv 0.5.0 and later),
# - and finally declare `/app` as the target for `uv sync`.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/code/.venv

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

# Since there's no point in shipping lock files, we move them
# into a directory that is NOT copied into the runtime image.
# The trailing slash makes COPY create `/_lock/` automagically.
COPY pyproject.toml uv.lock /_lock/

# Synchronize dependencies.
# This layer is cached until uv.lock or pyproject.toml change.
RUN --mount=type=cache,target=/root/.cache \
    cd /_lock && \
    uv sync \
      --frozen \
      --no-default-groups \
      --group prod \
      --compile-bytecode

FROM node:24 AS build-node
RUN nodejs -v && npm -v
WORKDIR /code

# keep in sync with tailwind.config.js
COPY *.json *.js .babelrc /code/
COPY config/settings.py /code/config/settings.py
COPY templates /code/templates/
COPY assets /code/assets/

RUN npm install
RUN npm run build

FROM python:3.13-slim-bullseye
ENV PYTHONUNBUFFERED=1
ENV DEBUG=0

RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update \
    && apt-get install -y \
    # psycopg2 dependencies
    libpq5 \
    # Translations dependencies
    gettext \
    # audio/video dependencies
    ffmpeg \
    # Azure cognitive audio dependencies
    build-essential libssl-dev ca-certificates libasound2 wget \
    # curl for heroku log shipping
    curl \
    # mimetype detection (creates /etc/mime.types)
    mailcap \
    # mimetype detection from content
    libmagic1 \
    # psql client for dbshell
    postgresql-client \
    # cleaning up unused files
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false

RUN addgroup --system django \
    && adduser --system --ingroup django django

WORKDIR /code
COPY --from=build-node /code/static /code/static
COPY --from=build-python --chown=django:django /code /code
# make sure we use the virtualenv python/gunicorn/celery by default
ENV PATH="/code/.venv/bin:$PATH"

COPY --chown=django:django . /code

ARG SECRET_KEY
ARG DJANGO_ALLOWED_HOSTS

RUN SECRET_KEY=${SECRET_KEY} DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS} python manage.py collectstatic --noinput --settings=config.settings_production
RUN chown django:django -R static_root

USER django

ENV PORT=8000

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 config.wsgi:application"]
