"""Seeded builders for the benchmark scenarios. Each builder returns data only; it does not time anything."""

import random
from dataclasses import dataclass

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.models import Pipeline
from apps.pipelines.tests.utils import (
    code_node,
    create_runnable,
    end_node,
    llm_response_with_prompt_node,
    render_template_node,
    start_node,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory

SEED = 20261008

# Session history sizes (the "session history axis" of the plan).
HISTORY_NEW = 0
HISTORY_TYPICAL = 20
HISTORY_LONG = 300

_WORDS = ["lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "elit", "sed", "do"]


@dataclass
class Bench:
    pipeline: Pipeline
    session: ExperimentSession
    provider_id: str
    model_id: str


def make_bench(history_size: int = HISTORY_NEW) -> Bench:
    """Create the DB rows shared by every scenario: team-scoped provider, model, pipeline and session."""
    provider = LlmProviderFactory.create()
    model = LlmProviderModelFactory.create(team=provider.team)
    session = ExperimentSessionFactory.create(team=provider.team)
    add_history(session, history_size)
    return Bench(
        pipeline=PipelineFactory.create(team=provider.team),
        session=session,
        provider_id=str(provider.id),
        model_id=str(model.id),
    )


def add_history(session: ExperimentSession, count: int, words_per_message: int = 25) -> None:
    """Add ``count`` alternating human/AI messages with fixed-seed content, identical on every run."""
    rng = random.Random(SEED)
    messages = [
        ChatMessage(
            chat=session.chat,
            message_type=ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI,
            content=" ".join(rng.choices(_WORDS, k=words_per_message)),
        )
        for i in range(count)
    ]
    ChatMessage.objects.bulk_create(messages)


def llm_node(bench: Bench, name: str, **kwargs) -> dict:
    return llm_response_with_prompt_node(bench.provider_id, bench.model_id, name=name, **kwargs)


def single_llm_nodes(bench: Bench, **kwargs) -> list[dict]:
    """P1: start -> one LLM node -> end."""
    return [start_node(), llm_node(bench, "llm", **kwargs), end_node()]


def linear_chain_nodes(bench: Bench, **kwargs) -> list[dict]:
    """P2: python, LLM, render, LLM in sequence."""
    return [
        start_node(),
        code_node("def main(input, **kwargs):\n    return input.upper()", name="code"),
        llm_node(bench, "llm1", **kwargs),
        render_template_node("{{ input }} | rendered", name="render"),
        llm_node(bench, "llm2", **kwargs),
        end_node(),
    ]


def deep_chain_nodes(bench: Bench, length: int = 25) -> list[dict]:
    """P5: ``length`` nodes in sequence."""
    passthrough = "def main(input, **kwargs):\n    return input"
    middle = [
        render_template_node("{{ input }}", name=f"render{i}") if i % 2 else code_node(passthrough, name=f"code{i}")
        for i in range(length)
    ]
    return [start_node(), *middle, end_node()]


def build_runnable(bench: Bench, nodes: list[dict]):
    return create_runnable(bench.pipeline, nodes)
