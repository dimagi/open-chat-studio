/** What a test starts from. Each state includes the ones before it (see `apps/web/e2e_data.py`). */
export type E2EState = "team" | "llm_provider" | "chatbot" | "session";

/**
 * The scenarios global setup creates, each with its own user and team, and the state each starts from.
 * Give each test its own scenario, so no test sees data another test changed.
 */
export const SCENARIOS = {
  login: "team",
  createTeam: "team",
  addLlmProvider: "team",
  createChatbot: "llm_provider",
  chatWithChatbot: "chatbot",
  findSession: "session",
  readTranscript: "session",
} as const satisfies Record<string, E2EState>;

export type ScenarioName = keyof typeof SCENARIOS;

/** Global setup stores the seeded scenarios in this env var as JSON, and the test workers inherit it. */
export const E2E_SCENARIOS_ENV = "E2E_SCENARIOS";

/** What every scenario has. */
interface TeamData {
  email: string;
  password: string;
  team_slug: string;
  team_url: string;
  session_cookie: { name: string; value: string };
}

interface LlmProviderData extends TeamData {
  llm_provider_name: string;
}

interface ChatbotData extends LlmProviderData {
  chatbot_name: string;
  chatbot_url: string;
}

interface SessionData extends ChatbotData {
  session_url: string;
  user_message: string;
}

interface DataByState {
  team: TeamData;
  llm_provider: LlmProviderData;
  chatbot: ChatbotData;
  session: SessionData;
}

/** One scenario's data, with only the fields its state creates. */
export type E2EData<Name extends ScenarioName = ScenarioName> = DataByState[(typeof SCENARIOS)[Name]];
