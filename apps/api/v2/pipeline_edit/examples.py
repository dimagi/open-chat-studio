"""Per-node-type request examples for the node write endpoints (#4140).

``params`` is a free-form object in the schema -- what it may hold depends on the ``type``, and one
endpoint serves every type -- so the schema alone cannot say what a body for a given type looks
like. The POST examples do, one per served type, each naming *every* param that type declares. Two
things they say that it cannot: a param's name is the node type's own rather than the
``/pipeline/options/`` list it draws from (``source_material_id`` is the param, ``source_material``
the list), and every id here is a placeholder for one that endpoint serves.

The PATCH examples are partial bodies instead. Its params are the POST ones, so a full payload per
type there would only repeat the same eight payloads under a second verb.

Held to the node schemas by ``tests/test_schema_examples.py``.
"""

from typing import NamedTuple

from drf_spectacular.utils import OpenApiExample

#: ``node type -> every param that type declares``. Resource ids are placeholders; the rest are
#: realistic, since an example is read as a starting point.
FULL_PARAMS: dict[str, dict] = {
    "CodeNode": {
        "name": "trim_answer",
        "code": "def main(input: str, **kwargs) -> str:\n    return input.strip()\n",
        "tag": "trimmed",
    },
    "RenderTemplate": {
        "name": "format_reply",
        "template_string": "Hi {{ participant_data.name }} -- {{ input }}",
        "tag": "formatted",
    },
    "SendEmail": {
        "name": "email_transcript",
        "recipient_list": "support@example.test, escalations@example.test",
        "subject": "Transcript for {{ participant_details.identifier }}",
        "body": "{{ input }}",
        "tag": "emailed",
    },
    "ExtractStructuredData": {
        "name": "extract_order",
        "llm_provider_id": 4,
        "llm_provider_model_id": 12,
        "llm_model_parameters": {"temperature": 0.2},
        "data_schema": '{"order_number": "the order the participant is asking about"}',
        "tag": "extracted",
    },
    "ExtractParticipantData": {
        "name": "remember_order",
        "llm_provider_id": 4,
        "llm_provider_model_id": 12,
        "llm_model_parameters": {"temperature": 0.2},
        "data_schema": '{"order_number": "the order the participant is asking about"}',
        "key_name": "orders",
        "tag": "remembered",
    },
    "RouterNode": {
        "name": "triage",
        "llm_provider_id": 4,
        "llm_provider_model_id": 12,
        "llm_model_parameters": {"temperature": 0.0},
        "prompt": "Route on what {participant_data} is asking for.",
        "keywords": ["schedule", "reschedule", "cancel"],
        "default_keyword_index": 2,
        "tag_output_message": True,
        "history_type": "named",
        "history_name": "triage-history",
        "history_mode": "max_history_length",
        "user_max_token_limit": 4000,
        "max_history_length": 25,
    },
    "StaticRouterNode": {
        "name": "route_on_plan",
        "keywords": ["free", "pro"],
        "default_keyword_index": 0,
        "tag_output_message": True,
        "data_source": "session_state",
        "route_key": "plan",
    },
    "LLMResponseWithPrompt": {
        "name": "answer",
        "llm_provider_id": 4,
        "llm_provider_model_id": 12,
        "llm_model_parameters": {"temperature": 0.4},
        "prompt": (
            "You are a support agent. Answer from {source_material}, the files in {media} and "
            "whichever of {collection_index_summaries} fits. You are talking to "
            "{participant_data} and it is now {current_datetime}."
        ),
        "history_type": "named",
        "history_name": "support-history",
        "history_mode": "summarize",
        "user_max_token_limit": 8000,
        "max_history_length": 30,
        "source_material_id": 7,
        "collection_id": 3,
        "collection_index_ids": [8, 9],
        "max_results": 5,
        "generate_citations": False,
        "tools": ["update-user-data", "one-off-reminder", "calculator"],
        "custom_actions": ["5:weather_get"],
        "built_in_tools": ["web-search", "code-execution"],
        "tool_config": {
            "web-search": {"allowed_domains": ["docs.example.test"], "blocked_domains": ["forum.example.test"]}
        },
        "synthetic_voice_id": 21,
        "tag": "answered",
    },
}

#: Each type's example summary, kept beside the params rather than in them so the payloads stay
#: readable as payloads.
NOTES: dict[str, str] = {
    "CodeNode": "`code` must define `main(input, **kwargs)`.",
    "RenderTemplate": "Jinja2 template, using `{{ ... }}`.",
    "SendEmail": "`recipient_list` is comma-separated; subject/body are Jinja2 templates.",
    "ExtractStructuredData": "`data_schema` maps field name -> what to look for.",
    "ExtractParticipantData": "As ExtractStructuredData, plus `key_name` to nest the result under.",
    "RouterNode": "`keywords[i]` routes to handle `output_i`; keywords are uppercased on save.",
    "StaticRouterNode": "Routes on stored data instead of an LLM; takes no LLM params.",
    "LLMResponseWithPrompt": "A normal LLM node, with every optional param set.",
}

MINIMAL_CREATE = OpenApiExample(
    name="Minimal",
    summary="`type` on its own: the node is created with that type's defaults, for PATCH to fill in.",
    value={"type": "CodeNode"},
    request_only=True,
)


def create_examples() -> list[OpenApiExample]:
    """One POST body per node type, each naming every param that type declares."""
    return [
        MINIMAL_CREATE,
        *(
            OpenApiExample(
                name=node_type,
                summary=f"{node_type}: {NOTES[node_type]}",
                value={"type": node_type, "label": _label(node_type), "params": params},
                request_only=True,
            )
            for node_type, params in FULL_PARAMS.items()
        ),
    ]


class UpdateBody(NamedTuple):
    """One documented PATCH body, and the node type it is a body for.

    The type is carried so ``tests/test_schema_examples.py`` can create a node to send it to; the
    schema itself has no use for it, since PATCH takes the type from the node.
    """

    name: str
    node_type: str
    summary: str
    value: dict


#: Partial bodies, one per thing a PATCH does. Deliberately not a full payload per node type the way
#: :data:`FULL_PARAMS` gives POST: the params mean the same either way, so repeating all eight would
#: only say a second time what the create examples already say.
UPDATE_BODIES: list[UpdateBody] = [
    UpdateBody(
        name="One param",
        node_type="CodeNode",
        summary="Params merge key by key, so a body can be one key wide.",
        value={"params": {"code": "def main(input: str, **kwargs) -> str:\n    return input.upper()\n"}},
    ),
    UpdateBody(
        name="Label only",
        node_type="CodeNode",
        summary="No `params` at all: the node's configuration is left alone.",
        value={"label": "Trim the answer"},
    ),
    UpdateBody(
        name="Router keywords",
        node_type="RouterNode",
        summary="Regenerates the router's output handles, and takes its edges with them.",
        value={"params": {"keywords": ["schedule", "reschedule", "cancel"]}},
    ),
]


def update_examples() -> list[OpenApiExample]:
    """One partial PATCH body per thing a PATCH does.

    The full param set per type is what the POST examples document; a reader after one type's
    vocabulary goes to ``GET /pipeline/nodes/{node_type}/`` either way.
    """
    return [
        OpenApiExample(name=body.name, summary=body.summary, value=body.value, request_only=True)
        for body in UPDATE_BODIES
    ]


def _label(node_type: str) -> str:
    """A display name for the example's node. Free text -- the UI builder shows it, nothing reads it."""
    return f"My {node_type}"
