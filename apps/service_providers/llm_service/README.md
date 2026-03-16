

# LLM Service
The LLM service layer is designed as a unified abstraction layer that enables OCS to interact with multiple LLM providers (OpenAI, Anthropic, etc.) through a consistent API while handling provider-specific features, authentication, and model parameters 

For product-level concepts and model configuration guidance, see the user guide on [LLM Concepts](https://docs.openchatstudio.com/concepts/llm/). 

## Design Intention
- **Extensible:** Easy to add new providers and new LLM functionality as this changes rapidly
- **Unified Model Management:** Centralized handling of configuration, retries, usage tracking, request shaping etc
- **Capability-Based Feature Support:** Different providers support different features. 

Its design follows the service provider pattern described in [apps/service_providers/README.md](../README.md).

The code is the source of truth so check [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio)

## Provider-Specific Libraries
LangChain is a core dependency in this package: it provides the common chat model interfaces, message structures, and callback hooks that let one LLM service layer work across OpenAI, Anthropic, etc.

Beyond LangChain's abstraction layer, the module uses official provider SDKs for direct API access when needed. For example:

- **OpenAI SDK** — Direct access to OpenAI's API for features not yet wrapped by LangChain (file handling, advanced features)
- **Anthropic SDK** — Direct access for provider-specific operations and token counting
- **Google Cloud Libraries** — Vertex AI authentication and model access
- **tiktoken** — OpenAI's token encoding library for calculating token counts (used for cost tracking and context window management)

## Risks
 - Complexity of different providers (configuration parameters, validation rules, rate limiting, retry etc)
 - Rapidly changing LLM APIs and SDKs
 - Inconsistent feature support as not all providers support the same capabilities 
 - LLM model deprecation 
 - Tricky error handling
 - Runtime incompatibilities when providers change behavior

