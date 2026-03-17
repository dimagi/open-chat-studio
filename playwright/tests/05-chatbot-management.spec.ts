import { test, expect } from '@playwright/test';
import { login, TEAM_SLUG } from '../helpers/common';

const CHATBOTS_URL = `/a/${TEAM_SLUG}/chatbots/`;

test.describe('Chatbot Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);

  });

  test('Create a chatbot', async ({ page }) => {
    const chatbotName = `E2E Bot ${Date.now()}`;
    const chatbotDescription = 'A chatbot created by Playwright E2E test';

    // Navigate to Chatbots list
    await page.goto(CHATBOTS_URL);
    await expect(page.getByRole('heading', { name: 'Chatbots' })).toBeVisible();


    // Click Add New button
    await page.getByRole('button', { name: 'Add New' }).click();

    // Verify the create dialog appears
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'Create a new Chatbot' })).toBeVisible();

    // Fill in Name and Description
    await dialog.getByRole('textbox', { name: 'Name' }).fill(chatbotName);
    await dialog.getByRole('textbox', { name: 'Description' }).fill(chatbotDescription);

    // Click Create Chatbot
    await dialog.getByRole('button', { name: 'Create Chatbot' }).click();

    // After creation, we should be redirected to the edit page (pipeline editor)
    await expect(page).toHaveURL(/\/chatbots\/\d+\/edit\//, { timeout: 10000 });

    // Creation confirmed by redirect to edit page above; no need to check the paginated list
  });

  test('View chatbot details', async ({ page }) => {
    // Navigate to Chatbots list
    await page.goto(CHATBOTS_URL);


    // Show all chatbots including archived
    await page.getByRole('checkbox', { name: 'Show Archived' }).check();
    await page.waitForSelector('.htmx-indicator', { state: 'hidden', timeout: 10000 });

    // Click on a chatbot name in the list
    const chatbotLink = page.getByRole('table').getByRole('link').first();
    const chatbotName = await chatbotLink.textContent();
    await chatbotLink.click();

    // Verify detail page loaded
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);

    // Verify chatbot name heading
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    // Verify channels section
    await expect(page.getByRole('heading', { name: 'Channels:' })).toBeVisible();

    // Verify tabs exist
    await expect(page.getByRole('tab', { name: 'Sessions' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Versions' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Settings' })).toBeVisible();
  });

  test('Edit chatbot pipeline', async ({ page }) => {
    const chatbotName = `Pipeline Bot ${Date.now()}`;

    // Create a chatbot first
    await page.goto(CHATBOTS_URL);

    await page.getByRole('button', { name: 'Add New' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('textbox', { name: 'Name' }).fill(chatbotName);
    await dialog.getByRole('button', { name: 'Create Chatbot' }).click();
    await expect(page).toHaveURL(/\/chatbots\/\d+\/edit\//, { timeout: 10000 });

    // Verify pipeline editor loaded with React Flow canvas
    await expect(page.locator('.react-flow')).toBeVisible({ timeout: 10000 });

    // Verify nodes exist (Input, LLM, Output)
    await expect(page.getByRole('button', { name: /Input/ }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /Output/ }).last()).toBeVisible();

    // Click the Add Node button (+ icon) to verify the node panel opens
    await page.getByTitle('Add Node').click();
    const addNodeDialog = page.getByRole('dialog').filter({ hasText: 'Available Nodes' });
    await expect(addNodeDialog).toBeVisible();
    await expect(addNodeDialog.getByRole('heading', { name: 'Available Nodes' })).toBeVisible();

    // Verify available node types are listed
    await expect(addNodeDialog.getByText('LLM', { exact: true })).toBeVisible();
    await expect(addNodeDialog.getByText('LLM Router')).toBeVisible();

    // Verify the save indicator is present (shown as a tooltip on the save status button)
    await expect(page.locator('[data-tip="Saved"]')).toBeVisible({ timeout: 15000 });

    // Verify zoom controls
    await expect(page.getByRole('button', { name: 'zoom in' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'zoom out' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'fit view' })).toBeVisible();
  });

  test('Edit chatbot settings', async ({ page }) => {
    const chatbotName = `Settings Bot ${Date.now()}`;
    const updatedName = `${chatbotName} Updated`;
    const updatedDescription = 'Updated description via E2E test';

    // Create a chatbot first
    await page.goto(CHATBOTS_URL);

    await page.getByRole('button', { name: 'Add New' }).click();
    const createDialog = page.getByRole('dialog');
    await createDialog.getByRole('textbox', { name: 'Name' }).fill(chatbotName);
    await createDialog.getByRole('textbox', { name: 'Description' }).fill('Original description');
    await createDialog.getByRole('button', { name: 'Create Chatbot' }).click();
    await expect(page).toHaveURL(/\/chatbots\/(\d+)\/edit\//, { timeout: 10000 });

    // Extract chatbot URL from the edit URL and navigate to detail page
    const editUrl = page.url();
    const detailUrl = editUrl.replace('/edit/', '/');
    await page.goto(detailUrl);
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);

    // Click the Settings tab
    await page.getByRole('tab', { name: 'Settings' }).click();

    // Verify settings content is visible
    await expect(page.getByRole('heading', { name: 'Name', level: 4 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Description', level: 4 })).toBeVisible();

    // Click the Edit button
    await page.getByRole('button', { name: 'Edit' }).click();

    // Modify settings
    await page.getByRole('textbox', { name: 'Name' }).fill(updatedName);
    await page.getByRole('textbox', { name: 'Description' }).fill(updatedDescription);

    // Save
    await page.getByRole('button', { name: 'Save' }).click();

    // Verify the updated name appears in the heading
    await expect(page.getByRole('heading', { name: updatedName, level: 1 })).toBeVisible();
  });

  test('Archive a chatbot', async ({ page }) => {
    const chatbotName = `Archive Bot ${Date.now()}`;

    // Create a chatbot first
    await page.goto(CHATBOTS_URL);

    await page.getByRole('button', { name: 'Add New' }).click();
    const createDialog = page.getByRole('dialog');
    await createDialog.getByRole('textbox', { name: 'Name' }).fill(chatbotName);
    await createDialog.getByRole('button', { name: 'Create Chatbot' }).click();
    await expect(page).toHaveURL(/\/chatbots\/(\d+)\/edit\//, { timeout: 10000 });

    // Navigate to the chatbot detail page
    const editUrl = page.url();
    const detailUrl = editUrl.replace('/edit/', '/');
    await page.goto(detailUrl);

    // Go to Settings tab
    await page.getByRole('tab', { name: 'Settings' }).click();

    // Click Archive
    page.on('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'Archive' }).click();

    // Should redirect to chatbots list
    await expect(page).toHaveURL(/\/chatbots\/$/, { timeout: 10000 });

    // Navigate back to the detail page to confirm the chatbot is now archived
    // (The list is paginated and newly-created bots sort last by activity; avoid fragile list search)
    // On the detail page, archived chatbots show the name heading + a separate "Archived" badge
    await page.goto(detailUrl);
    await expect(
      page.getByRole('heading', { name: chatbotName, level: 1 })
    ).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Archived')).toBeVisible();
  });

  test('Copy a chatbot', async ({ page }) => {
    const chatbotName = `Copy Bot ${Date.now()}`;

    // Create a chatbot first
    await page.goto(CHATBOTS_URL);

    await page.getByRole('button', { name: 'Add New' }).click();
    const createDialog = page.getByRole('dialog');
    await createDialog.getByRole('textbox', { name: 'Name' }).fill(chatbotName);
    await createDialog.getByRole('button', { name: 'Create Chatbot' }).click();
    await expect(page).toHaveURL(/\/chatbots\/(\d+)\/edit\//, { timeout: 10000 });

    // Navigate to the chatbot detail page
    const editUrl = page.url();
    const detailUrl = editUrl.replace('/edit/', '/');
    await page.goto(detailUrl);
    await expect(page.getByRole('heading', { name: chatbotName, level: 1 })).toBeVisible();

    // Click the Copy button (icon-only button with class 'copy-button', tooltip 'Copy')
    await page.locator('button.copy-button').click();

    // Verify the copy dialog appears with pre-filled name
    const copyDialog = page.getByRole('dialog');
    await expect(copyDialog).toBeVisible();
    await expect(copyDialog.getByRole('heading', { name: 'Copy Chatbot' })).toBeVisible();
    await expect(copyDialog.getByRole('textbox', { name: /New Chatbot Name/ })).toHaveValue(
      `${chatbotName} (copy)`
    );

    // Click Confirm
    await copyDialog.getByRole('button', { name: 'Confirm' }).click();

    // Should redirect to the new copied chatbot's detail page
    await expect(page).toHaveURL(/\/chatbots\/\d+\//, { timeout: 10000 });
    await expect(
      page.getByRole('heading', { name: `${chatbotName} (copy)`, level: 1 })
    ).toBeVisible();
  });

});
