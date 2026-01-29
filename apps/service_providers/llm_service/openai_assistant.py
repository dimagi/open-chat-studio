"""OpenAI Assistant Runnable with fixes for the Assistants v2 API.

This module is intentionally isolated to allow lazy loading of the heavy
langchain_classic dependency only when needed.
"""

from __future__ import annotations

import contextlib
from time import sleep
from typing import Any

from langchain_classic.agents.openai_assistant import OpenAIAssistantRunnable as BrokenOpenAIAssistantRunnable
from langchain_core.callbacks import CallbackManager, dispatch_custom_event
from langchain_core.load import dumpd
from langchain_core.runnables import RunnableConfig, ensure_config


class OpenAIAssistantRunnable(BrokenOpenAIAssistantRunnable):
    """Fixed OpenAI Assistant Runnable for the Assistants v2 API.

    This is a temporary solution to fix langchain's compatibility with the assistants v2 API.
    This code is copied from:
    `https://github.com/langchain-ai/langchain/blob/54adcd9e828e24bb24b2055f410137aca6a12834/libs/langchain/
    langchain/agents/openai_assistant/base.py#L256`.
    and updated so that the thread API gets an `attachments` key instead of the previous `file_ids` key.
    TODO: Here's a PR that tries to fix it in LangChain: https://github.com/langchain-ai/langchain/pull/21484
    """

    def invoke(self, input: dict, config: RunnableConfig | None = None):
        config = ensure_config(config)
        callback_manager = CallbackManager.configure(
            inheritable_callbacks=config.get("callbacks"),
            inheritable_tags=config.get("tags"),
            inheritable_metadata=config.get("metadata"),
        )
        run_manager = callback_manager.on_chain_start(dumpd(self), input, name=config.get("run_name"))
        try:
            # Being run within AgentExecutor and there are tool outputs to submit.
            if self.as_agent and input.get("intermediate_steps"):
                tool_outputs = self._parse_intermediate_steps(input["intermediate_steps"])
                run = self.client.beta.threads.runs.submit_tool_outputs(**tool_outputs)
            # Starting a new thread and a new run.
            elif "thread_id" not in input:
                thread = {
                    "messages": [
                        {
                            "role": "user",
                            "content": input["content"],
                            "attachments": input.get("attachments", {}),
                            "metadata": input.get("message_metadata"),
                        }
                    ],
                    "metadata": input.get("thread_metadata"),
                }
                run = self._create_thread_and_run(input, thread)
            # Starting a new run in an existing thread.
            elif "run_id" not in input:
                _ = self.client.beta.threads.messages.create(
                    input["thread_id"],
                    content=input["content"],
                    role="user",
                    attachments=input.get("attachments", {}),
                    metadata=input.get("message_metadata"),
                )
                run = self._create_run(input)
            # Submitting tool outputs to an existing run, outside the AgentExecutor
            # framework.
            else:
                run = self.client.beta.threads.runs.submit_tool_outputs(**input)
            with contextlib.suppress(RuntimeError):
                dispatch_custom_event(
                    "OpenAI Assistant Run Created",
                    {
                        "assistant_id": run.assistant_id,
                        "thread_id": run.thread_id,
                        "run_id": run.id,
                    },
                )
            run = self._wait_for_run(run.id, run.thread_id)
        except BaseException as e:
            run_manager.on_chain_error(e)
            raise e
        try:
            response = self._get_response(run)
        except BaseException as e:
            run_manager.on_chain_error(e, metadata=run.dict())
            raise e
        else:
            run_manager.on_chain_end(response)
            return response

    def _wait_for_run(self, run_id: str, thread_id: str, progress_states=("in_progress", "queued")) -> Any:
        in_progress = True
        while in_progress:
            run = self.client.beta.threads.runs.retrieve(run_id, thread_id=thread_id)
            in_progress = run.status in progress_states
            if in_progress:
                sleep(self.check_every_ms / 1000)
        return run
