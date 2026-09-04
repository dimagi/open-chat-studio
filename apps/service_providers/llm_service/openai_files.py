"""Uploading files to OpenAI and pulling them back down.

These helpers used to live in ``apps.assistants.sync``, but nothing here is specific to the
OpenAI Assistants API — they are ordinary Files API calls. The live consumer is
``OpenAIRemoteIndexManager`` (remote vector-store indexes for Collections), which outlives the
Assistants feature, so they moved here ahead of that app's removal. See issue #4254.
"""

import logging
from io import BytesIO

import openai
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from apps.files.models import File

logger = logging.getLogger("ocs.openai_files")


def create_files_remote(client, files):
    file_ids = []
    for file in files:
        if not file.external_id:
            _push_file_to_openai(client, file)
        file_ids.append(file.external_id)
    return file_ids


def _push_file_to_openai(client, file: File):
    with file.file.open("rb") as fh:
        bytesio = BytesIO(fh.read())
    openai_file = _openai_create_file_with_retries(client, file.name, bytesio)
    file.external_id = openai_file.id
    file.external_source = "openai"
    file.save()


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    retry=retry_if_exception_type(openai.RateLimitError),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.INFO),
)
def _openai_create_file_with_retries(client, filename, bytesio):
    logger.debug("Creating file in OpenAI: %s", filename)
    # "assistants" is a Files API purpose value, not a dependency on the Assistants API. Whether
    # OpenAI keeps honouring it after the Assistants retirement is unverified — see issue #4254.
    return client.files.create(file=(filename, bytesio), purpose="assistants")
