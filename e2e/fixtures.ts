import { type APIRequestContext, type APIResponse, test as base } from "@playwright/test";

import { E2E_BASE_URL } from "./config";

/** What a test starts from. Each state includes the ones before it. */
export type E2EState = "team" | "llm_provider" | "chatbot" | "session";

/** Returned by `/e2e-data/create/`. Fields after `session_cookie` exist only from the state that adds them. */
export interface E2EData {
  email: string;
  password: string;
  team_slug: string;
  team_url: string;
  session_cookie: { name: string; value: string };
  llm_provider_name: string;
  chatbot_name: string;
  chatbot_url: string;
  session_url: string;
  user_message: string;
}

interface E2EFixtures {
  /** Create a new user and team in `state`. Unless `logIn` is false, the page is logged in as that user. */
  createData: (state: E2EState, options?: { logIn?: boolean }) => Promise<E2EData>;
}

export const test = base.extend<E2EFixtures>({
  createData: async ({ context, request }, use) => {
    await use(async (state, { logIn = true } = {}) => {
      const response = await request.post("/e2e-data/create/", { form: { state } });
      await checkOk(response);
      const data: E2EData = await response.json();
      if (logIn) {
        await context.addCookies([{ ...data.session_cookie, url: E2E_BASE_URL }]);
      }
      return data;
    });
    // Pages keep sending requests, such as the dashboard's charts, that write rows referencing the team.
    // Closing them stops new ones; cleanup retries while the last ones finish.
    for (const page of context.pages()) {
      await page.close();
    }
    // Cleanup deletes every E2E user, which is why the tests run one at a time.
    await cleanUp(request);
  },
});

export async function cleanUp(request: APIRequestContext) {
  await checkOk(await request.post("/e2e-data/cleanup/"));
}

async function checkOk(response: APIResponse) {
  if (!response.ok()) {
    throw new Error(`${response.url()} returned ${response.status()}: ${await response.text()}`);
  }
}

export { expect } from "@playwright/test";
