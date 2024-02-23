import pytest

from apps.service_providers.llm_service.runnables import (
    ChainOutput,
    GenerationCancelled,
    SimpleExperimentRunnable,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import FakeLlm, FakeLlmService


@pytest.fixture()
def fake_llm():
    return FakeLlm(responses=[["This", " is", " a", " test", " message"]], token_counts=[30, 20, 10])


@pytest.fixture()
def session(fake_llm):
    session = ExperimentSessionFactory()
    session.experiment.get_llm_service = lambda: FakeLlmService(llm=fake_llm)
    session.experiment.tools_enabled = True
    return session


@pytest.mark.django_db()
def test_simple(session, fake_llm):
    runnable = SimpleExperimentRunnable(session=session, experiment=session.experiment)
    _test_runnable(runnable, session)


def _test_runnable(runnable, session):
    original_build = runnable._build_chain

    def _new_build():
        chain = original_build()
        orig_stream = chain.stream

        def _stream(*args, **kwargs):
            """Simulate a cancellation after the 2nd token."""
            for i, token in enumerate(orig_stream(*args, **kwargs)):
                if i == 2:
                    session.chat.metadata = {"cancelled": True}
                    session.chat.save(update_fields=["metadata"])
                yield token

        chain.__dict__["stream"] = _stream
        return chain

    runnable.__dict__["_build_chain"] = _new_build

    with pytest.raises(GenerationCancelled) as exc_info:
        runnable.invoke("hi")
    assert exc_info.value.output == ChainOutput(output="This is a", prompt_tokens=30, completion_tokens=20)
