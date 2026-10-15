import { E2E_BASE_URL } from "../config";
import { expect, test } from "../fixtures";

// Each test uses its own scenario (see `e2e/scenarios.ts`), so the tests run in any order.

test("log in", { annotation: { type: "user-stories", description: "1" } }, async ({ page, scenario }) => {
  const data = await scenario("login", { logIn: false });
  await page.goto("/accounts/login/");
  await page.locator("input[name=login]").fill(data.email);
  // With the SSO login flag on, the password field appears after submitting the email.
  const continueButton = page.locator("input[type=submit][value=Continue]");
  if (await continueButton.isVisible()) {
    await continueButton.click();
  }
  await page.locator("input[name=password]").fill(data.password);
  await page.locator("input[type=submit][value='Sign In']").click();
  await expect(page).toHaveURL(new RegExp(`/a/${data.team_slug}/dashboard/`));
});

test("create a team", { annotation: { type: "user-stories", description: "2" } }, async ({ page, scenario }) => {
  await scenario("createTeam");
  const teamName = `E2E Team ${Date.now()}`;
  await page.goto("/teams/create/");
  await page.locator("input[name=name]").fill(teamName);
  await page.locator("input[type=submit][value=Save]").click();
  await expect(page).toHaveURL(/\/a\/[^/]+\/team\/$/);
  await expect(page.locator("h1[data-cy=title]")).toHaveText(teamName);
  // Only a team admin sees the invite button.
  await page.goto(`${page.url()}section/members/`);
  await expect(page.locator("[data-cy=btn-invite]")).toBeVisible();
});

test(
  "add an OpenAI LLM provider",
  { annotation: { type: "user-stories", description: "3" } },
  async ({ page, scenario }) => {
    const data = await scenario("addLlmProvider");
    const providerName = "My OpenAI";
    await page.goto(`${data.team_url}/service_providers/llm/create/openai/`);
    await page.locator("input[name=name]").fill(providerName);
    // The dev server's mock LLM answers in place of OpenAI, so no real key or network call is needed.
    await page.locator("input[name=openai_api_key]").fill("mock");
    await page.locator("input[name=openai_api_base]").fill(`${E2E_BASE_URL}/mock-llm/v1`);
    await page.getByRole("button", { name: "Create and Verify" }).click();
    await expect(page).toHaveURL(new RegExp(`${data.team_url}/team/$`));
    const providerRow = page.getByRole("row").filter({ hasText: providerName });
    await expect(providerRow).toContainText("LLM & embedding");
    await expect(providerRow).toContainText("Verified");
  },
);

test(
  "create a chatbot",
  { annotation: { type: "user-stories", description: "4" } },
  async ({ page, scenario }) => {
    const data = await scenario("createChatbot");
    const chatbotName = "Created in E2E";
    await page.goto(`${data.team_url}/chatbots/`);
    await page.getByText("Add New", { exact: true }).click();
    await page.locator("#new_chatbot_form input[name=name]").fill(chatbotName);
    await page.getByRole("button", { name: "Create Chatbot" }).click();
    await expect(page).toHaveURL(new RegExp(`${data.team_url}/chatbots/\\d+/edit/`));
    // The team has one LLM provider, so a selected provider and model is that provider's.
    await page.getByRole("button", { name: "Advanced", exact: true }).click();
    const llmDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Editing LLM" }) });
    const modelSelect = llmDialog.locator("select").filter({ has: page.getByRole("option", { name: "Select a model" }) });
    await expect(modelSelect).toHaveValue(/^\d+\|:\|\d+$/);
    await page.goto(page.url().replace(/edit\/$/, ""));
    await expect(page.locator("#chatbot-name")).toHaveText(chatbotName);
  },
);

test(
  "chat with the chatbot",
  { annotation: { type: "user-stories", description: "5" } },
  async ({ page, scenario }) => {
    const data = await scenario("chatWithChatbot");
    const userMessage = "Reply with a short greeting.";
    const chatResponses: { status: number; url: string }[] = [];
    page.on("response", (response) => {
      if (response.url().includes("/api/chat/")) {
        chatResponses.push({ status: response.status(), url: response.url() });
      }
    });
    await page.goto(data.chatbot_url);
    await page.getByRole("button", { name: "Chat to the bot" }).hover();
    await page.getByRole("button", { name: "Unreleased version" }).click();
    const widget = page.locator("#chatbot-widget");
    await widget.locator("textarea.message-textarea").fill(userMessage);
    await widget.locator("button.send-button").click();
    await expect(widget.locator(".message-row-user")).toContainText(userMessage);
    // "short" makes the mock LLM answer with one lorem ipsum sentence. Checking for it rules out
    // the "Sorry, something went wrong" reply the chatbot sends when the LLM call fails.
    await expect(widget.locator(".message-row-assistant .chat-markdown")).toHaveText(/^[A-Z][a-z]+( [a-z]+){7,17}\.\s*$/);
    expect(chatResponses).not.toHaveLength(0);
    expect(chatResponses.filter((response) => response.status >= 400)).toEqual([]);
  },
);

test(
  "find the session in the sessions list",
  { annotation: { type: "user-stories", description: "6" } },
  async ({ page, scenario }) => {
    const data = await scenario("findSession");
    await page.goto(data.chatbot_url);
    const sessionsTable = page.locator("#sessions-table");
    await expect(sessionsTable).toContainText(data.email);
    await sessionsTable.getByRole("link", { name: "Session Details" }).click();
    await expect(page).toHaveURL(new RegExp(`${data.session_url}$`));
  },
);

test("read the transcript", { annotation: { type: "user-stories", description: "7" } }, async ({ page, scenario }) => {
  const data = await scenario("readTranscript");
  await page.goto(data.session_url);
  const messages = page.locator("#messages-container");
  await messages.scrollIntoViewIfNeeded();
  await expect(messages.locator(".chat-message-user .message-contents")).toContainText(data.user_message);
  await expect(messages.locator(".chat-message-system .message-contents")).not.toBeEmpty();
});
