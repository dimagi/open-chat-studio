import { test as base } from "@playwright/test";

import { E2E_BASE_URL } from "./config";
import { E2E_SCENARIOS_ENV, type E2EData, type ScenarioName } from "./scenarios";

interface E2EFixtures {
  /** Return the scenario's data. Unless `logIn` is false, the page is logged in as the scenario's user. */
  scenario: <Name extends ScenarioName>(name: Name, options?: { logIn?: boolean }) => Promise<E2EData<Name>>;
}

export const test = base.extend<E2EFixtures>({
  scenario: async ({ context }, use) => {
    await use(async <Name extends ScenarioName>(name: Name, { logIn = true } = {}) => {
      const data = readScenarios()[name] as E2EData<Name> | undefined;
      if (!data) {
        throw new Error(`Global setup created no data for scenario "${name}".`);
      }
      if (logIn) {
        await context.addCookies([{ ...data.session_cookie, url: E2E_BASE_URL }]);
      }
      return data;
    });
  },
});

function readScenarios(): Partial<Record<ScenarioName, E2EData>> {
  const json = process.env[E2E_SCENARIOS_ENV];
  if (!json) {
    throw new Error(`${E2E_SCENARIOS_ENV} is not set. Global setup sets it; run the tests through \`pnpm e2e\`.`);
  }
  return JSON.parse(json);
}

export { expect } from "@playwright/test";
