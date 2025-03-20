from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tracers import LangChainTracer

from . import TraceService

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


class LangSmithTraceService(TraceService):
    def get_callback(self, participant_id: str, session_id: str) -> BaseCallbackHandler:
        from langsmith import Client

        client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

        return LangChainTracer(client=client, project_name=self.config["project"])
