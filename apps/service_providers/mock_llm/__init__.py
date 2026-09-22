"""Mock LLM provider for Open Chat Studio local development.

Serves the OpenAI-compatible endpoints OCS calls and answers with lorem ipsum, so a dev
environment can exercise chatbots and pipelines end to end without spending money or
needing an API key. The endpoints are part of the dev server itself, so OCS calls back
into the process it is running in; they are mounted only when ``settings.DEBUG`` is on.

Endpoints, under ``/mock-llm/``:
    v1/responses            the Responses API, which OCS's "openai" provider type uses
    v1/chat/completions     used by the provider types that route through
                            OpenAIGenericService, such as LiteLLM, Groq and OpenRouter
    v1/models

Wiring it into OCS:
    ``manage.py bootstrap_data`` creates a provider named "Stub LLM" pointing here, along
    with a model named "stub". To add one by hand instead: Service Providers > LLM > add,
    choose type "OpenAI", set API Base URL to http://localhost:8000/mock-llm/v1 and any
    non-empty API key, then add a custom model named "stub".

Directives:
    The last user message is scanned, case-insensitively and on word boundaries, for the
    keywords below. They combine freely, e.g. "slow 3 long" or "slow 2 error 429".

    short                 reply with one sentence
    medium                reply with two paragraphs (the default)
    long                  reply with six paragraphs
    slow                  wait 5 seconds before replying
    slow <n>              wait <n> seconds before replying, capped at 120
    error                 fail with HTTP 500
    error <status>        fail with that HTTP status, which must be a 4xx or 5xx
    error <code>          fail with the status that code implies, one of:
                          insufficient_quota, credit_balance_exhausted,
                          rate_limit_exceeded, invalid_api_key, model_not_found,
                          context_length_exceeded

    The error codes are the ones apps/service_providers/llm_service/error_classification.py
    reads, so they are the way to drive OCS down its billing, authentication, not-found and
    context-overflow branches.

    Keywords are matched anywhere in the message, so an ordinary question that happens to
    contain "how long..." gets a long answer. "error" is the one to watch: a message like
    "I got an error yesterday" fails the call with a 500. That is the trade for not
    needing a prefix.

Structured output and tool calls:
    A request that asks for JSON matching a schema is answered with a value built from
    that schema rather than with prose, since prose would fail the caller's validation.
    This covers ``response_format`` on chat completions, ``text.format`` on the Responses
    API, and a forced tool call (``tool_choice`` of "required" or a named function), which
    is how OCS's router node and its help agents ask for structured answers.

    The value is filled in deterministically: the first enum member, the lowest allowed
    number, the shortest allowed array, lorem words for free-text strings. A router
    schema constrains its field to the configured keywords, so every message routes down
    the first branch. That is arbitrary but visible, which a silent fall back to the
    default route is not.

Limitations:
    Under ``tool_choice: "auto"`` the mock answers with text and never elects to call a
    tool, so a pipeline that depends on the model choosing a tool by itself won't take
    that branch. There is no embeddings endpoint. A "slow" directive holds a request
    thread for its duration, which a `runserver --nothreading` dev server does not have
    to spare.
"""
