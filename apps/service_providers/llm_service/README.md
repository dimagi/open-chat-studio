
# LLM Service

The LLM service layer is designed as a unified abstraction layer that enables OCS to interact with multiple LLM providers (OpenAI, Anthropic, etc.) through a consistent API while handling provider-specific features, authentication, and model parameters 

For product-level concepts and model configuration guidance, see the user guide on [LLM Concepts](https://docs.openchatstudio.com/concepts/llm/). 

The code is the source of truth [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio)

## Design 

- **Extensible:** Easy to add new providers and new LLM functionality as this changes rapidly
- **Unified Model Management:** Centralized handling of configuration, retries, usage tracking, request shaping etc
- **Capability-Based Feature Support:** Different providers support different features. 

Its design follows the service provider pattern described in [apps/service_providers/README.md](../README.md).

## Risks

 - Complexity of differenet providers (configuration parameters, validation rules, rate limiting, retry etc)
 - LLM model deprication and new models added
 - Inconsistent feature support as not all providers support the same capabilities 
 - Tricky error handling
 - Runtime errors caused by issues like incompatibity with new models


