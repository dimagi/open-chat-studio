import importlib

from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import (
    ConfigurableField,
    Runnable,
    RunnableConfig,
    RunnablePassthrough,
)
from langchain_openai.chat_models import ChatOpenAI

from apps.experiments.models import ExperimentSession
from apps.pipelines.graph import PipelineGraph
from apps.pipelines.nodes import ExperimentSessionId, PipelineNode


def output_session(node) -> Runnable:
    """Appends the session to the config to be used elsewhere"""

    class SessionPassthrough(RunnablePassthrough):
        session_id: int | None
        session: ExperimentSession | None = None

        class Config:
            arbitrary_types_allowed = True

        def invoke(self, input, config: RunnableConfig, **kwargs):
            session_id = config.get("configurable", {}).get("session_id")

            # We can't use patch_config because we need to modify the actual config dict in memory
            # Is this hacky? Sure.
            # new_config = patch_config(config, configurable={"session": )
            config.get("configurable", {})["session"] = ExperimentSession.objects.get(id=session_id)

            return super().invoke(input, config, **kwargs)

    return SessionPassthrough().configurable_fields(
        session_id=ConfigurableField(id="session_id", name="Session", description="The session id"),
        session=ConfigurableField(id="session", name="Session", description="The actual session"),
    )


def get_llm_runnable_with_session(node) -> Runnable:
    class LLMModelPassthrough(RunnablePassthrough):
        llm_model: str | None = None

        def invoke(self, input, config, **kwargs):
            session: ExperimentSession = config.get("configurable", {})["session"]
            config.get("configurable", {}).update(
                {"llm_model": session.experiment.llm, "llm_temperature": session.experiment.temperature}
            )
            return super().invoke(input, config, **kwargs)

    return LLMModelPassthrough() | ChatOpenAI(
        temperature=0, openai_api_key=node.params["openai_api_key"]
    ).configurable_fields(
        temperature=ConfigurableField(
            id="llm_temperature",
            name="LLM Temperature",
            description="The temperature of the LLM",
        ),
        model_name=ConfigurableField(
            id="llm_model",
            name="LLM Model",
            description="The model to use with the LLM",
        ),
        # openai_api_key cannot be set at runtime: https://github.com/langchain-ai/langchain/issues/16567
    )


def get_prompt_runnable(node) -> Runnable:
    class PromptTemplatePassthrough(RunnablePassthrough):
        template: str = ""

        def invoke(self, input, config, **kwargs):
            session: ExperimentSession = config.get("configurable", {})["session"]
            config.get("configurable", {}).update({"template": session.experiment.prompt_text})
            return super().invoke(input, config, **kwargs)

    return PromptTemplatePassthrough() | PromptTemplate.from_template("Say hello to the {topic}").configurable_fields(
        template=ConfigurableField(id="template", name="Template", description="The promt template")
    )


RUNNABLE_FUNCTIONS = {
    "llm": get_llm_runnable_with_session,
    "prompt": get_prompt_runnable,
    "session": output_session,
}


def build_runnable(graph: PipelineGraph, session_id: str | None = None) -> Runnable:
    # builds the final runnable from the graph.
    # assume a single ordered graph to start
    # the node is an LLM runnable
    all_nodes = importlib.import_module("apps.pipelines.nodes")
    runnable = RunnablePassthrough()
    for node in graph.nodes:
        node_class = getattr(all_nodes, node.type)
        if _requires_session(node_class) and session_id is None:
            raise ValueError("The pipeline requires a session_id, but none was passed in")

        if _requires_session(node_class):
            new_runnable = getattr(all_nodes, node.type).build(node, session_id)
        else:
            new_runnable = getattr(all_nodes, node.type).build(node)
        runnable |= new_runnable

    return runnable


def _requires_session(node: PipelineNode):
    return any(field.type_ == ExperimentSessionId for field in node.__fields__.values())
