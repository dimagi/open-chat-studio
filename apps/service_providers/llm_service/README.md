
# LLM Service
The LLM service layer is designed as a unified abstraction layer that enables OCS to interact with multiple LLM providers (OpenAI, Anthropic, etc.) through a consistent API while handling provider-specific features, authentication, and model parameters

Refer to the [user guide](https://docs.openchatstudio.com/concepts/team/llm_providers) on product-level features as a background to this design.

## Design Intention
- **Extensible:** Easy to add new providers, [new models](https://developers.openchatstudio.com/developer_guides/managing_models/) and new LLM functionality as this changes rapidly
- **Unified Model Management:** Centralized handling of configuration, retries, usage tracking, request shaping etc
- **Reuse via OpenAI-API Compatibility:** OpenAI, Groq, Perplexity, DeepSeek, MiniMax and Azure all expose an OpenAI-compatible API, so they're implemented as thin configurations of the same OpenAI SDK/LangChain client rather than needing their own integration.
- LLM Provider **Capability-Based Feature Support:** Different providers can optionally support features like built-in tools, audio transcription, prompt caching, reasoning/thinking parameters, RAG, file citations, and history.
- **Model lifecycle management:** Because models are frequently added and deprecated, the process for doing so needs to be easy and must not impact users.

Its design follows the service provider pattern described in [apps/service_providers/README.md](../README.md).

The code is the source of truth so check [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio)

## Provider-Specific Libraries
LangChain is a core dependency in this package: it provides the common chat model interfaces, message structures, and callback hooks that let one LLM service layer work across OpenAI, Anthropic, etc.

Beyond LangChain's abstraction layer, the module uses official provider SDKs for direct API access when needed. For example:
- **OpenAI SDK** — Direct access to OpenAI's API for features not yet wrapped by LangChain (file handling,vector store, audio transcription and other advanced features)
- **Anthropic SDK** — Direct access to Anthropic's exception types for provider-specific retry/rate-limit handling
- **Google Cloud Libraries** — Vertex AI authentication and model access
- **tiktoken** — OpenAI's token encoding library as a fallback for token counting when a provider's response carries no usage_metadata. Supports cost tracking and context window management

## Risks
 - Complexity of different providers (configuration parameters, validation rules, rate limiting, retry etc)
 - Rapidly changing LLM APIs and SDKs
 - Inconsistent feature support as not all providers support the same capabilities
 - LLM model deprecation
 - Tricky error handling
 - Runtime incompatibilities when providers change behavior
