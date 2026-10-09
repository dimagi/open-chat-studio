import { expect, type Page, test } from "@playwright/test";

import { E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD, E2E_TEAM_SLUG } from "../config";


// Each story builds on the one before it, so the tests share one logged-in page and stop at the first failure.
test.describe.configure({ mode: "serial" });

test.describe("core flow", () => {
  const teamName = `E2E Team ${Date.now()}`;
  const chatbotName = `Core flow bot ${Date.now()}`;
  const userMessage = "Reply with a short greeting.";
  let page: Page;
  // The team created in US-2; every later story runs inside it.
  let teamUrl: string;
  let chatbotUrl: string;
  let sessionUrl: string;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
  });

  test.afterAll(async () => {
    await page.close();
  });

  test("log in", { annotation: { type: "user-stories", description: "1" } }, async () => {
    await page.goto("/accounts/login/");
    await page.locator("input[name=login]").fill(E2E_EMAIL);
    // With the SSO login flag on, the password field appears after submitting the email.
    const continueButton = page.locator("input[type=submit][value=Continue]");
    if (await continueButton.isVisible()) {
      await continueButton.click();
    }
    await page.locator("input[name=password]").fill(E2E_PASSWORD);
    await page.locator("input[type=submit][value='Sign In']").click();
    await expect(page).toHaveURL(new RegExp(`/a/${E2E_TEAM_SLUG}/dashboard/`));
  });

  test("create a team", { annotation: { type: "user-stories", description: "2" } }, async () => {
    await page.goto("/teams/create/");
    await page.locator("input[name=name]").fill(teamName);
    await page.locator("input[type=submit][value=Save]").click();
    await expect(page).toHaveURL(/\/a\/[^/]+\/team\/$/);
    teamUrl = new URL(page.url()).pathname.replace(/team\/$/, "").replace(/\/$/, "");
    await expect(page.locator("h1[data-cy=title]")).toHaveText(teamName);
  });

  test("add an OpenAI LLM provider", { annotation: { type: "user-stories", description: "3" } }, async () => {
    await page.goto(`${teamUrl}/service_providers/llm/create/openai/`);
    await page.locator("input[name=name]").fill("OpenAI");
    // The dev server's mock LLM answers in place of OpenAI, so no real key or network call is needed.
    await page.locator("input[name=openai_api_key]").fill("mock");
    await page.locator("input[name=openai_api_base]").fill(`${E2E_BASE_URL}/mock-llm/v1`);
    await page.getByRole("button", { name: "Create and Verify" }).click();
    await expect(page).toHaveURL(new RegExp(`${teamUrl}/team/$`));
  });

  test("create a chatbot", { annotation: { type: "user-stories", description: "4" } }, async () => {
    await page.goto(`${teamUrl}/chatbots/`);
    await page.getByText("Add New", { exact: true }).click();
    await page.locator("#new_chatbot_form input[name=name]").fill(chatbotName);
    await page.getByRole("button", { name: "Create Chatbot" }).click();
    await expect(page).toHaveURL(new RegExp(`${teamUrl}/chatbots/\\d+/edit/`));
    chatbotUrl = page.url().replace(/edit\/$/, "");
    await page.goto(chatbotUrl);
    await expect(page.locator("#chatbot-name")).toHaveText(chatbotName);
  });

  test("chat with the chatbot", { annotation: { type: "user-stories", description: "5" } }, async () => {
    await page.goto(chatbotUrl);
    await page.getByRole("button", { name: "Chat to the bot" }).hover();
    await page.getByRole("button", { name: "Unreleased version" }).click();
    const widget = page.locator("#chatbot-widget");
    await widget.locator("textarea.message-textarea").fill(userMessage);
    await widget.locator("button.send-button").click();
    await expect(widget.locator(".message-row-user")).toContainText(userMessage);
    await expect(widget.locator(".message-row-assistant .chat-markdown")).not.toBeEmpty();
  });

  test("find the session in the sessions list", { annotation: { type: "user-stories", description: "6" } }, async () => {
    await page.goto(chatbotUrl);
    const sessionsTable = page.locator("#sessions-table");
    await expect(sessionsTable).toContainText(E2E_EMAIL);
    await sessionsTable.getByRole("link", { name: "Session Details" }).click();
    await expect(page).toHaveURL(/\/s\/[^/]+\/view\//);
    sessionUrl = page.url();
  });

  test("read the transcript", { annotation: { type: "user-stories", description: "7" } }, async () => {
    await page.goto(sessionUrl);
    const messages = page.locator("#messages-container");
    await messages.scrollIntoViewIfNeeded();
    await expect(messages.locator(".chat-message-user .message-contents")).toContainText(userMessage);
    await expect(messages.locator(".chat-message-system .message-contents")).not.toBeEmpty();
  });
});
