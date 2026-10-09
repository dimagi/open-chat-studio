# Core User Stories

The flows the product must always support. Each story is covered by a [Playwright test](developer_guides/testing/playwright_tests.md).

| #    | Story                                                                                                   | Covered by                      |
|------|---------------------------------------------------------------------------------------------------------|---------------------------------|
| US-1 | As a team member, I can log in with my email and password and land on my team's dashboard.             | `e2e/tests/core_flow.spec.ts`   |
| US-2 | As a team member, I can create a new team, and I become its admin.                                      | `e2e/tests/core_flow.spec.ts`   |
| US-3 | As a team admin, I can add an OpenAI LLM provider by entering an API key.                               | `e2e/tests/core_flow.spec.ts`   |
| US-4 | As a team admin, I can create a chatbot, and it gets a default pipeline that uses the team's LLM provider. | `e2e/tests/core_flow.spec.ts`   |
| US-5 | As a team admin, I can chat with the chatbot from its page and get a reply from the LLM.                | `e2e/tests/core_flow.spec.ts`   |
| US-6 | As a team admin, I can see my chat session in the chatbot's sessions list.                              | `e2e/tests/core_flow.spec.ts`   |
| US-7 | As a team admin, I can open the session and read the transcript, including my message and the reply.    | `e2e/tests/core_flow.spec.ts`   |
