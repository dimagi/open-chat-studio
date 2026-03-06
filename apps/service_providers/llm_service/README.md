
# LLM Service

This module provides the backend implementation for LLM provider management in Open Chat Studio.  The code here implements provider selection, configuration, and runtime integration for all supported LLM APIs.
**For conceptual background, supported models, and configuration options, see the [LLM Concepts documentation](https://docs.openchatstudio.com/concepts/llm/).**

## Architecture

This module follows the design as documented in [apps/service_providers/README.md](../README.md).

## Supported LLM Providers

| Provider              | Service Class             | Features                                                                                      |
|-----------------------|--------------------------|-----------------------------------------------------------------------------------------------|
| **OpenAI**            | `OpenAILlmService`       | Transcription, Assistants API, Vector stores, Streaming, Function calling, File operations     |
| **Azure OpenAI**      | `AzureLlmService`        | Azure-specific authentication, Deployment management, Regional endpoints, Model versioning     |
| **Anthropic**         | `AnthropicLlmService`    | Claude models, Extended context windows, System prompt support, Function calling, Token counting, Streaming      |
| **Groq**              | `OpenAIGenericService`   | OpenAI-compatible API wrapper, Model selection                          |
| **Perplexity**        | `OpenAIGenericService`   | OpenAI-compatible API wrapper, Perplexity models                              |
| **DeepSeek**          | `DeepSeekLlmService`     | DeepSeek-specific models, Large context windows, Streaming                                    |
| **Google Gemini**     | `GoogleLlmService`       | Gemini models, Local embedding index (RAG), Streaming, System prompt support                  |
| **Google Vertex AI**  | `GoogleVertexAILlmService`| Service account authentication, Regional deployment, gRPC/REST transport, Model management    |


## Key Components

### Core Service Class: `LlmService`
Abstract base class extending `pydantic.BaseModel`. Defines the interface all provider implementations must follow:

- `get_chat_model(llm_model, **kwargs)` — Returns a LangChain `BaseChatModel` instance
- `get_callback_handler(model)` — Returns a callback handler for token counting/monitoring
- `get_raw_client()` — Returns the raw provider API client
- `attach_built_in_tools(tools, config)` — Integrates provider-specific [tool](https://docs.openchatstudio.com/concepts/tools/?h=tools)/function calling
- `get_assistant(assistant_id)` — Retrieves deployed assistants (if supported)
- `transcribe_audio(audio)` — Speech-to-text conversion (if supported)

### Data Models
Structured data models for representing LLM chat responses and related artifacts.
Its core class, `LlmChatResponse` encapsulates the text output from an LLM along with sets of cited and generated files, providing a unified container for both the response and its associated resources

### Supporting modules

- **`adapters.py`** — Encapsulate provider-specific logic and context management, enabling flexible, reusable integration points for [pipelines](https://docs.openchatstudio.com/concepts/pipelines/), and chat workflows.
- **`parsers.py`** — Output parsing logic for different LLM response formats
- **`prompt_context.py`** — Dynamic prompt template context injection (ie [Prompt variables](https://docs.openchatstudio.com/concepts/prompt_variables/) )
- **`index_managers.py`** — Vector store and embedding index management and supports [RAG](https://docs.openchatstudio.com/concepts/collections/indexed/?h=index) workflows
- **`runnables.py`** — LangChain `Runnable` implementations for chat workflows (history management, [tools](https://docs.openchatstudio.com/concepts/tools/?h=tools), file and citation handling, prompt formatting)
- **`history_managers.py`** — Conversation [history management](https://docs.openchatstudio.com/concepts/sessions/#history-management) and compression
- **`model_parameters.py`** — LLM Model-specific parameter configuration
- **`retry.py`** — Retry logic for resilient API calls (ie [Rate Limiting](https://docs.openchatstudio.com/api/?h=rate#rate-limiting) )
- **`openai_assistant.py`** — OpenAI [Assistant](https://docs.openchatstudio.com/concepts/assistants/)-specific implementations.
- **`token_counters.py`** - supporting multiple providers for extracting token usage from model responses, text, and message lists for accurate cost tracking, context window management, and efficient message history compression


## Core Technologies

### LangChain
The module uses **LangChain** for fundamental LLM operations:
- `BaseChatModel` — Base class for chat model implementations
- `BaseCallbackHandler` — Hooks for monitoring and logging LLM interactions
- Message types (`HumanMessage`, `AIMessage`, etc.) — Message formatting and handling
- `PromptTemplate` — Dynamic prompt construction and variable substitution

### Provider-Specific Libraries
Beyond LangChain's abstraction layer, the module uses official provider SDKs for direct API access when needed:

- **tiktoken** — OpenAI's token encoding library for calculating token counts (used for cost tracking and context window management)
- **OpenAI SDK** — Direct access to OpenAI's API for features not yet wrapped by LangChain (file handling, advanced features)
- **Anthropic SDK** — Direct access for provider-specific operations and token counting
- **Google Cloud Libraries** — Vertex AI authentication and model access

## Integration Points

- **Pipelines** — use LLM nodes use adapters (`adapters.py`) to invoke services within DAG workflows
- **Chat Sessions** — use message history and context management via `history_managers.py`
- **[Evaluations](https://docs.openchatstudio.com/concepts/evaluations/?h=evalua)** — use token counting and cost tracking via callback handlers
- **Vector Search** — uses Index management for RAG-enabled workflows
- **Assistants** — use OpenAI and other provider-specific [assistant](https://docs.openchatstudio.com/concepts/assistants/) deployments
