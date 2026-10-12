import { request } from "@playwright/test";

import { E2E_BASE_URL } from "./config";
import { cleanUp } from "./fixtures";

export default async function globalSetup() {
  // Delete the data a run that stopped early left behind.
  const context = await request.newContext({ baseURL: E2E_BASE_URL });
  await cleanUp(context);
  await context.dispose();
}
