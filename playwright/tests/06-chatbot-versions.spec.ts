import { test, expect, Page } from '@playwright/test';
import { login } from '../helpers/common';

async function navigateToChatbotVersions(page: Page) {
  await page.getByRole('link', { name: 'Chatbots' }).first().click();
  await expect(page).toHaveURL(/\/chatbots\/$/);

  // Click the first chatbot in the list
  await page.getByRole('table').getByRole('link').first().click();
  await expect(page).toHaveURL(/\/chatbots\/\d+\//);



  // Click the Versions tab
  await page.getByRole('tab', { name: 'Versions' }).click();
}

test.describe('Chatbot Versions', () => {
  test('View versions table with expected columns', async ({ page }) => {
    await login(page);
    await navigateToChatbotVersions(page);

    // Verify the versions table is visible with the expected column headers
    const table = page.getByRole('table');
    await expect(table).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Version Number' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Created On' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Description' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Published' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Archived' })).toBeVisible();

    // Verify at least one version row exists (excluding the unreleased row)
    const versionRows = table.getByRole('row').filter({ hasNot: page.getByText('(unreleased)') });
    // Header row + at least one data row
    await expect(versionRows.first()).toBeVisible();
  });

  test('Create a new version', async ({ page }) => {
    await login(page);

    // Navigate to chatbot list
    await page.getByRole('link', { name: 'Chatbots' }).first().click();
    await expect(page).toHaveURL(/\/chatbots\/$/);

    // Get the first chatbot's URL to derive the edit URL
    const chatbotLink = page.getByRole('table').getByRole('link').first();
    const chatbotHref = await chatbotLink.getAttribute('href');
    await chatbotLink.click();
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);

    // Navigate to the edit page to make a pipeline change (required for version creation)
    await page.goto(`${chatbotHref}edit/`);
    await expect(page).toHaveURL(/\/chatbots\/\d+\/edit\//);


    // Wait for the pipeline editor to load
    await expect(page.locator('.react-flow')).toBeVisible({ timeout: 10000 });

    // Find the LLM node prompt and modify it to create a diff for versioning
    // The expand button is an icon-only button inside the 'Prompt' label area of the LLM node
    const expandPromptButton = page.locator('.fieldset').filter({ hasText: 'Prompt' }).locator('button.btn-ghost').first();
    await expandPromptButton.click();

    // Wait for the prompt dialog to appear (TextEditorModal with CodeMirror editor)
    const dialog = page.locator('dialog.modal').filter({ hasText: 'prompt' }).first();
    await expect(dialog).toBeVisible();

    // Modify the prompt text by appending a unique change using the CodeMirror editor
    const promptEditor = dialog.locator('.cm-content');
    const timestamp = Date.now();
    await promptEditor.click();
    await page.keyboard.press('End');
    await page.keyboard.type(` [E2E test change ${timestamp}]`);

    // Close the dialog
    await dialog.getByRole('button', { name: '✕' }).click();

    // Wait for auto-save (save status is shown as a tooltip on the save button, not visible text)
    await expect(page.locator('[data-tip="Saved"]')).toBeVisible({ timeout: 15000 });

    // Navigate back to the chatbot details page via breadcrumb
    await page.goto(`${chatbotHref}#versions`);
    await expect(page).toHaveURL(/\/chatbots\/\d+\//);


    // Click the Versions tab
    await page.getByRole('tab', { name: 'Versions' }).click();

    // Count existing version rows before creating a new one
    const versionsTable = page.getByRole('tabpanel').getByRole('table');
    await expect(versionsTable).toBeVisible();
    const existingRows = versionsTable.getByRole('row').filter({ hasNot: page.getByText('(unreleased)') });
    // Subtract 1 for header row
    const initialRowCount = await existingRows.count() - 1;

    // Click "+ Create Version"
    await page.getByRole('link', { name: '+ Create Version' }).click();
    await expect(page).toHaveURL(/\/versions\/create$/);

    // Verify the create version form is shown
    await expect(page.getByRole('heading', { name: 'Create New Version' })).toBeVisible();

    // Fill in the version description
    const versionDescription = `E2E version test ${timestamp}`;
    await page.getByRole('textbox', { name: 'Version description' }).fill(versionDescription);

    // Click Create button
    await page.getByRole('button', { name: 'Create' }).click();

    // Should redirect back to chatbot details with versions tab
    await expect(page).toHaveURL(/\/chatbots\/\d+\/#versions/);

    // The button should change to "Creating Version" (disabled) while the version
    // is being created asynchronously, then the new version row should appear
    // without a page reload (via HTMX polling)
    const creatingButton = page.getByRole('button', { name: /Creating Version/ });
    const createVersionLink = page.getByRole('link', { name: '+ Create Version' });

    // Wait for either the "Creating Version" button to appear briefly,
    // or for the version to already be created (fast creation)
    await expect(creatingButton.or(createVersionLink)).toBeVisible({ timeout: 5000 });

    // Wait for the new version to appear in the table (up to 60 seconds for async Celery task)
    await expect(page.getByRole('cell', { name: versionDescription })).toBeVisible({ timeout: 60000 });

    // Verify the new version row has the expected columns
    const newVersionRow = page.getByRole('row').filter({ hasText: versionDescription });
    await expect(newVersionRow).toBeVisible();

    // Verify version number is present (should be a number)
    const versionNumberCell = newVersionRow.getByRole('cell').first();
    await expect(versionNumberCell).toContainText(/\d+/);

    // Verify created date is present
    const createdOnCell = newVersionRow.getByRole('cell').nth(1);
    await expect(createdOnCell).not.toBeEmpty();

    // Verify there are now more version rows than before
    const updatedTable = page.getByRole('tabpanel').getByRole('table');
    const updatedRows = updatedTable.getByRole('row').filter({ hasNot: page.getByText('(unreleased)') });
    const finalRowCount = await updatedRows.count() - 1;
    expect(finalRowCount).toBeGreaterThan(initialRowCount);
  });
});
