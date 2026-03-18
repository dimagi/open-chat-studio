import { test, expect } from '@playwright/test';
import { setupPage, TEAM_SLUG } from '../helpers/common';

const CHATBOT_NAME = `My First Chatbot ${Date.now()}`;

// Helper to hide debug toolbar
async function hideDebugToolbar(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    const el = document.getElementById('djDebug');
    if (el) el.style.display = 'none';
  });
}

test.describe.serial('Flow 1: Create and Test a Chatbot', () => {
  test.setTimeout(60000);

  let chatbotDetailUrl: string;

  test('create a new chatbot and verify pipeline nodes', async ({ page }) => {
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/chatbots/`);
    await hideDebugToolbar(page);

    // Click "Add New" to open the creation dialog
    await page.getByRole('button', { name: 'Add New' }).click();

    // Fill in chatbot name, leave description empty
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('textbox', { name: 'Name' }).fill(CHATBOT_NAME);
    await dialog.getByRole('button', { name: 'Create Chatbot' }).click();

    // Should redirect to the pipeline editor
    await expect(page).toHaveURL(/\/chatbots\/\d+\/edit\//, { timeout: 10000 });

    // Wait for React Flow pipeline canvas to render
    const pipelineCanvas = page.locator('.react-flow');
    await expect(pipelineCanvas).toBeVisible({ timeout: 15000 });

    // Verify the three default nodes
    await expect(pipelineCanvas.getByText('LLM').first()).toBeVisible({ timeout: 10000 });
    await expect(pipelineCanvas.getByText('Input').first()).toBeVisible();
    await expect(pipelineCanvas.getByText('Output').first()).toBeVisible();

    // Extract chatbot detail URL from breadcrumb
    const breadcrumbLink = page.locator('[aria-label="breadcrumbs"] a').filter({ hasText: CHATBOT_NAME });
    chatbotDetailUrl = await breadcrumbLink.getAttribute('href') ?? '';
  });

  test('verify chatbot is in Draft mode', async ({ page }) => {
    await setupPage(page);
    await page.goto(chatbotDetailUrl);
    await hideDebugToolbar(page);

    // Verify the chatbot heading is visible
    await expect(page.getByRole('heading', { name: CHATBOT_NAME })).toBeVisible();

    // Go to Versions tab to verify "(unreleased)" row exists
    await page.getByRole('tab', { name: 'Versions' }).click();
    await expect(page.getByText('(unreleased)')).toBeVisible();
  });

  test('chat with a published chatbot', async ({ page }) => {
    test.setTimeout(120000);
    await setupPage(page);

    // Use the pre-existing "My first chatbot" (ID 1011) that has a published version
    await page.goto(`/a/${TEAM_SLUG}/chatbots/1011/`);
    await hideDebugToolbar(page);

    // Go to Versions tab and click the "Start web chat" button for the published version
    await page.getByRole('tab', { name: 'Versions' }).click();

    // Find the published version row (has ✓ in the Published column) and click its chat button
    const publishedRow = page.locator('table tbody tr').filter({ hasText: '✓' }).first();
    await expect(publishedRow).toBeVisible({ timeout: 5000 });
    // Click the "Start web chat" button (inside .tooltip within .inline)
    await publishedRow.locator('.inline > .tooltip > .btn').click({ force: true });

    // Should navigate to the chat session page
    await expect(page).toHaveURL(/\/session\/\d+\//, { timeout: 10000 });

    // Wait for chat interface to load
    const messageInput = page.getByRole('textbox', { name: 'Message' });
    await expect(messageInput).toBeVisible({ timeout: 10000 });

    // Helper to send a message and wait for response
    const sendMessage = async (text: string) => {
      await messageInput.fill(text);
      await page.getByRole('button', { name: 'Send' }).click();
      // Wait for response - the Send button becomes enabled again
      await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled({ timeout: 30000 });
    };

    // Exchange three messages
    await sendMessage('Hi');
    await sendMessage('Tell me a joke');
    await sendMessage("That's great");
  });

  test('verify session appears in chatbot sessions', async ({ page }) => {
    await setupPage(page);

    // Use the same pre-existing chatbot
    await page.goto(`/a/${TEAM_SLUG}/chatbots/1011/`);
    await hideDebugToolbar(page);

    // Click Sessions tab
    await page.getByRole('tab', { name: 'Sessions' }).click();

    // Verify there is at least one session in the table
    const sessionsPanel = page.getByRole('tabpanel');
    await expect(sessionsPanel.locator('table tbody tr').first()).toBeVisible({ timeout: 10000 });
  });
});
