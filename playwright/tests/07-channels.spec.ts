import { test, expect } from '@playwright/test';
import { login, TEAM_SLUG } from '../helpers/common';

test.describe('Channels', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);

  });

  test('Copy API channel URL', async ({ page, context }) => {
    // Grant clipboard permissions so the copy feedback appears
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);

    // Navigate to Chatbots
    await page.getByRole('link', { name: ' Chatbots' }).click();
    await expect(page).toHaveURL(/\/chatbots\//);

    // Click on a chatbot to go to its detail page
    const chatbotLink = page.getByRole('link', { name: 'Customer Support Bot' }).first();
    await expect(chatbotLink).toBeVisible();
    await chatbotLink.click();
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);

    // Click the API channel button
    const apiButton = page.getByRole('button', { name: /API/ });
    await expect(apiButton).toBeVisible();
    await apiButton.click();

    // Verify the "Copied!" feedback appears (may be button text or tooltip)
    await expect(page.getByText('Copied!')).toBeVisible({ timeout: 5000 });
  });

  test('Access Web channel', async ({ page }) => {
    // Navigate to a chatbot detail page
    await page.goto(`/a/${TEAM_SLUG}/chatbots/`);
    const chatbotLink = page.getByRole('link', { name: 'Customer Support Bot' }).first();
    await chatbotLink.click();
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);

    // Click the Web channel button
    const webButton = page.getByRole('button', { name: /Web/ });
    await expect(webButton).toBeVisible();
    await webButton.click();

    // Verify the Web channel dropdown appears with Share and Invitations options
    // 'Share' is rendered as a <li><span> (not a link), 'Invitations' is an <a> link
    await expect(page.getByText('Share').first()).toBeVisible();
    await expect(page.getByRole('link', { name: /Invitations/ })).toBeVisible();
  });

  test('Add new channel shows available channel types', async ({ page }) => {
    // Navigate to a chatbot detail page
    await page.goto(`/a/${TEAM_SLUG}/chatbots/`);
    const chatbotLink = page.getByRole('link', { name: 'Customer Support Bot' }).first();
    await chatbotLink.click();
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);

    // Click the "+" button to see available channels

    const addButton = page.getByRole('button', { name: '+' });
    await expect(addButton).toBeVisible();
    await addButton.click();

    // Verify channel options are shown
    await expect(page.getByRole('button', { name: 'Telegram' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'WhatsApp' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Facebook' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Embedded Widget' })).toBeVisible();
  });

  test('Configure SureAdhere messaging provider and verify channel becomes available', async ({ page }) => {
    // First check if SureAdhere provider already exists, and delete it if so
    await page.goto(`/a/${TEAM_SLUG}/team/`);


    // Check if a SureAdhere provider already exists in the messaging providers table
    const existingSureAdhere = page.getByRole('cell', { name: 'SureAdhere' });
    if (await existingSureAdhere.isVisible({ timeout: 2000 }).catch(() => false)) {
      // Provider already exists, skip creation
    } else {
      // Navigate to create a new messaging provider
      await page.goto(`/a/${TEAM_SLUG}/service_providers/messaging/create/`);

      await expect(page.getByRole('heading', { name: 'Messaging Provider' })).toBeVisible();

      // Fill in the provider details
      await page.getByRole('textbox', { name: 'Name' }).fill('E2E SureAdhere Provider');
      await page.getByLabel('Type').selectOption('SureAdhere');

      // Fill in SureAdhere-specific fields
      await page.getByRole('textbox', { name: 'Client ID' }).fill('test-client-id');
      await page.getByRole('textbox', { name: 'Client Secret' }).fill('test-client-secret');
      await page.getByRole('textbox', { name: 'Client Scope' }).fill('test-scope');
      await page.getByRole('textbox', { name: 'Auth URL' }).fill('https://auth.example.com');
      await page.getByRole('textbox', { name: 'Base URL' }).fill('https://api.example.com');

      // Click Create
      await page.getByRole('button', { name: 'Create' }).click();

      // Verify redirected back to team page
      await expect(page).toHaveURL(new RegExp(`/a/${TEAM_SLUG}/team/`));
    }

    // Navigate to the chatbot detail page
    await page.goto(`/a/${TEAM_SLUG}/chatbots/`);
    const chatbotLink = page.getByRole('link', { name: 'Customer Support Bot' }).first();
    await chatbotLink.click();
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);

    // Click the "+" button to add a new channel

    const addButton = page.getByRole('button', { name: '+' });
    await addButton.click();

    // Verify SureAdhere is now available and enabled (not disabled)
    const sureAdhereButton = page.getByRole('button', { name: 'SureAdhere' });
    await expect(sureAdhereButton).toBeVisible();
    await expect(sureAdhereButton).toBeEnabled();
  });
});
