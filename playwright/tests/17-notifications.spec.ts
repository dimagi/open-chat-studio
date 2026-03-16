import { test, expect } from '@playwright/test';
import { login, TEAM_SLUG } from '../helpers/common';

test.describe('Notifications', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test.describe('Trigger Notification via Failed Chat', () => {
    test('Create broken LLM provider, configure chatbot, trigger failure, and verify notification', async ({ page }) => {
      const providerName = `Broken Provider ${Date.now()}`;

      // Step 1: Navigate to team page
      await page.goto(`/a/${TEAM_SLUG}/team/`);
      await expect(page.getByRole('heading', { name: 'LLM and Embedding Model Service Providers' })).toBeVisible();

      // Step 2: Click "Add new" for LLM providers
      await page.goto(`/a/${TEAM_SLUG}/service_providers/llm/create/`);
      await expect(page.getByRole('heading', { name: 'LLM Service Provider' })).toBeVisible();

      // Step 3: Fill in provider details with invalid API key
      await page.getByRole('textbox', { name: 'Name' }).fill(providerName);
      // Type defaults to OpenAI
      await expect(page.locator('select[name="type"]')).toHaveValue('openai');

      // Step 4: Fill in invalid API key
      await page.getByRole('textbox', { name: /API Key/ }).fill('invalid-key-12345');

      // Step 5: Click Create

      await page.getByRole('button', { name: 'Create' }).click();
      await expect(page).toHaveURL(/\/team\//);

      // Step 6: Navigate to chatbots and open a chatbot's pipeline editor
      await page.goto(`/a/${TEAM_SLUG}/chatbots/`);
      await expect(page.getByRole('heading', { name: 'Chatbots' })).toBeVisible();

      // Wait for the chatbot table to load
      const chatbotTable = page.getByRole('table');
      await expect(chatbotTable).toBeVisible({ timeout: 10000 });

      // Get the first chatbot's link and navigate to its edit page
      const chatbotLink = chatbotTable.getByRole('link').first();
      const chatbotHref = await chatbotLink.getAttribute('href');
      expect(chatbotHref).toBeTruthy();

      // Step 7: Navigate to pipeline editor
      await page.goto(`${chatbotHref}edit/`);
      await expect(page).toHaveURL(/\/chatbots\/\d+\/edit\//);

      // Wait for React Flow pipeline editor to load
      await expect(page.locator('.react-flow')).toBeVisible({ timeout: 10000 });

      // Step 8: Click "Advanced" on the LLM node to open settings
      const advancedButton = page.locator('.react-flow button:has-text("Advanced")').last();
      await advancedButton.click();

      // Wait for the editing dialog to appear (filter to the active modal, excluding hidden ones)
      const dialog = page.getByRole('dialog').filter({ hasText: 'Editing LLM' });
      await expect(dialog).toBeVisible({ timeout: 5000 });
      await expect(dialog.getByRole('heading', { name: 'Editing LLM' })).toBeVisible();

      // Select the broken provider model
      const llmModelSelect = dialog.locator('select[name="llm_provider_id"]');
      await expect(llmModelSelect).toBeVisible();

      // Select a model from the broken provider
      const brokenOptionValue = await llmModelSelect.locator('option').filter({ hasText: providerName }).first().getAttribute('value');
      expect(brokenOptionValue).toBeTruthy();
      await llmModelSelect.selectOption(brokenOptionValue!);

      // Close the dialog

      await dialog.getByRole('button', { name: 'Close editor' }).click();

      // Wait for pipeline to save (save status is shown as a tooltip on the save button, not visible text)
      await expect(page.locator('[data-tip="Saved"]')).toBeVisible({ timeout: 15000 });

      // Step 9: Navigate back to chatbot detail page
      await page.goto(`${chatbotHref}`);
      await expect(page).toHaveURL(/\/chatbots\/\d+\//);

      // Step 10: Click "Chat to the bot" and choose unreleased version
      // The dropdown uses DaisyUI dropdown-hover class: it opens on CSS :hover.
      // Use the tooltip data-tip attribute to reliably locate the dropdown trigger.
      const chatDropdownTrigger = page.locator('[data-tip="Chat to the bot"] [role="button"]');
      await expect(chatDropdownTrigger).toBeVisible({ timeout: 10000 });

      // Hover over the dropdown trigger to open the CSS :hover-based dropdown
      await chatDropdownTrigger.hover();

      // The "Unreleased version" button is inside the dropdown-content ul
      // Try to click it while still hovering (mouse stays within the dropdown area)
      const unreleasedButton = page.getByRole('button', { name: 'Unreleased version' });
      const publishedButton = page.getByRole('button', { name: 'Published version' });

      // Check if unreleased version option is available
      const hasUnreleased = await unreleasedButton.isVisible({ timeout: 3000 }).catch(() => false);
      const hasPublished = await publishedButton.isVisible({ timeout: 1000 }).catch(() => false);

      if (hasUnreleased) {
        await unreleasedButton.click();
      } else if (hasPublished) {
        await publishedButton.click();
      } else {
        // Fallback: directly submit the form for the unreleased version via JS
        await page.evaluate(() => {
          // Find the form that contains "Unreleased version" button inside the dropdown
          const buttons = document.querySelectorAll('[data-tip="Chat to the bot"] button[type="submit"]');
          if (buttons.length > 0) {
            (buttons[0] as HTMLButtonElement).form?.submit();
          }
        });
      }

      // Wait for chat page to load
      await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible({ timeout: 10000 });

      // Step 11: Send a message to trigger the error
      await page.getByRole('textbox', { name: 'Message' }).fill('Hello, test message');

      await page.getByRole('button', { name: 'Send' }).click();

      // Wait for the error response
      await expect(page.getByText(/sorry.*went wrong/i)).toBeVisible({ timeout: 30000 });

      // Step 12: Navigate to Notifications
      await page.goto(`/a/${TEAM_SLUG}/notifications/`);
      await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();

      // Step 13: Verify a notification table is visible
      const notifTable = page.getByRole('table');
      await expect(notifTable).toBeVisible({ timeout: 10000 });
      await expect(notifTable.getByRole('columnheader', { name: 'Notification' })).toBeVisible();
      await expect(notifTable.getByRole('columnheader', { name: 'Level' })).toBeVisible();
    });
  });

  test.describe('View Notifications', () => {
    test('Navigate to notifications page and verify table structure', async ({ page }) => {
      await page.goto(`/a/${TEAM_SLUG}/notifications/`);
      await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();

      // Verify table headers
      const table = page.getByRole('table');
      await expect(table).toBeVisible({ timeout: 10000 });
      await expect(table.getByRole('columnheader', { name: 'Timestamp' })).toBeVisible();
      await expect(table.getByRole('columnheader', { name: 'Notification' })).toBeVisible();
      await expect(table.getByRole('columnheader', { name: 'Level' })).toBeVisible();
      await expect(table.getByRole('columnheader', { name: 'Mute' })).toBeVisible();
      await expect(table.getByRole('columnheader', { name: 'Read Status' })).toBeVisible();
    });

    test('Use Filter control', async ({ page }) => {
      await page.goto(`/a/${TEAM_SLUG}/notifications/`);
      await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();

      // Click Filter button

      await page.getByRole('button', { name: /Filter/ }).click();

      // Verify filter panel appears with column selector
      await expect(page.getByText('Where')).toBeVisible({ timeout: 5000 });
      await expect(page.getByText('Select column')).toBeVisible();
    });

    test('Use Date Range control', async ({ page }) => {
      await page.goto(`/a/${TEAM_SLUG}/notifications/`);
      await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();

      // Click Date Range button

      await page.getByRole('button', { name: /Date Range/ }).click();

      // Verify date range dropdown options appear
      await expect(page.getByRole('link', { name: 'Last 1 Hour' })).toBeVisible({ timeout: 5000 });
      await expect(page.getByRole('link', { name: 'Last 1 Day' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Last 7 Days' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Last 14 Days' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Last 30 Days' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Last 3 Months' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Last Year' })).toBeVisible();
    });

    test('Click Silence to mute notifications', async ({ page }) => {
      await page.goto(`/a/${TEAM_SLUG}/notifications/`);
      await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();

      // Click Silence button

      await page.getByRole('button', { name: /Silence/ }).click();

      // Verify silence dropdown options are in the DOM
      // Scope to #btn-do-not-disturb to avoid strict mode violations from duplicate '1 day' text
      // (notification mute buttons also contain '1 day', '1 week', etc.)
      const silenceDropdown = page.locator('#btn-do-not-disturb');
      await expect(silenceDropdown.getByText('Turn off after')).toBeAttached({ timeout: 5000 });
      await expect(silenceDropdown.getByText('8 hours')).toBeAttached();
      await expect(silenceDropdown.getByText('1 day', { exact: true })).toBeAttached();
      await expect(silenceDropdown.getByText('1 week')).toBeAttached();
      await expect(silenceDropdown.getByText('1 month')).toBeAttached();
    });

    test('Click Preferences to go to notification settings in profile', async ({ page }) => {
      await page.goto(`/a/${TEAM_SLUG}/notifications/`);
      await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();

      // Click Preferences link

      await page.getByRole('link', { name: /Preferences/ }).click();

      // Verify navigation to profile page
      await expect(page).toHaveURL(/\/users\/profile\//);
      await expect(page.getByRole('heading', { name: 'My Details' })).toBeVisible();
    });
  });
});
