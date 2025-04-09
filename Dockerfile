FROM python:3.11-slim-bullseye AS build-python
RUN apt-get update \
  # dependencies for building Python packages
  && apt-get install -y build-essential libpq-dev
COPY ./requirements /requirements
RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --no-cache-dir --no-deps --wheel-dir /wheels \
    -r /requirements/requirements.txt \
    -r /requirements/prod-requirements.txt

FROM node:22 AS build-node
RUN nodejs -v && npm -v
WORKDIR /code

# keep in sync with tailwind.config.js
COPY *.json *.js .babelrc /code/
COPY gpt_playground/settings.py /code/gpt_playground/settings.py
COPY templates /code/templates/
COPY assets /code/assets/

RUN npm install
RUN npm run build

FROM python:3.11-slim-bullseye
ENV PYTHONUNBUFFERED=1
ENV DEBUG=0

RUN apt-get update && apt-get install -y \
  # psycopg2 dependencies
  libpq-dev \
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
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

RUN addgroup --system django \
    && adduser --system --ingroup django django

COPY --from=build-node /code/static /code/static
COPY --from=build-python /wheels /wheels
COPY ./requirements /requirements
RUN pip install --no-index --find-links=/wheels \
    -r /requirements/requirements.txt \
    -r /requirements/prod-requirements.txt \
    && rm -rf /wheels \
    && rm -rf /root/.cache/pip/*

WORKDIR /code

COPY --chown=django:django . /code

RUN python manage.py collectstatic --noinput --settings=gpt_playground.settings_production
RUN chown django:django -R static_root

USER django

ENV PORT=8000

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 gpt_playground.wsgi:application"]
