import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Sessions', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('View All Sessions page with table and controls', async ({ page }) => {
    // Navigate to All sessions in the sidebar
    await page.getByRole('link', { name: 'All sessions' }).click();
    await expect(page).toHaveURL(/\/chatbots\/sessions\/$/);

    await expect(page.getByRole('heading', { name: 'All Sessions' })).toBeVisible();

    // Wait for the sessions table to load via HTMX
    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Verify table headers
    await expect(table.getByRole('columnheader', { name: 'Chatbot' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Participant' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Message Count' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Last activity' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'State' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Actions' })).toBeVisible();

    // Verify Filter and Date Range controls are present
    await expect(page.getByRole('button', { name: /Filter/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Date Range/ })).toBeVisible();

    // Verify at least one session row with a Session Details link
    await expect(table.getByRole('link', { name: 'Session Details' }).first()).toBeVisible();
  });

  test('Filter All Sessions by Status (complete)', async ({ page }) => {
    await page.getByRole('link', { name: 'All sessions' }).click();
    await expect(page).toHaveURL(/\/chatbots\/sessions\/$/);


    // Wait for table to load
    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });
    await expect(table.getByRole('link', { name: 'Session Details' }).first()).toBeVisible();

    // Click Filter button
    await page.getByRole('button', { name: /Filter/ }).click();
    await expect(page.getByText('Where')).toBeVisible();

    // Select Status column
    await page.locator('label').filter({ hasText: 'Select column' }).click();
    await page.getByText('Status', { exact: true }).click();

    // Verify "any of" operator is shown (use first() to avoid strict mode violation)
    await expect(page.getByText('any of').first()).toBeVisible();

    // Open value selector and select "complete"
    await page.locator('.relative > .w-40').click();
    await page.getByRole('checkbox', { name: 'complete' }).check();

    // Wait for filter to be applied
    await expect(page).toHaveURL(/filter_0_column=state/);
    await expect(page).toHaveURL(/filter_0_value=.*complete/);

    // Verify Filter (1) indicator appears
    await expect(page.getByRole('button', { name: /Filter \(1\)/ })).toBeVisible();

    // Wait for filtered table to load - since no sessions have "complete" status,
    // expect "No sessions yet!" message
    await expect(page.getByText('No sessions yet!')).toBeVisible({ timeout: 10000 });
  });

  test('Filter All Sessions by Chatbot', async ({ page }) => {
    await page.getByRole('link', { name: 'All sessions' }).click();
    await expect(page).toHaveURL(/\/chatbots\/sessions\/$/);


    // Wait for table to load
    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });
    await expect(table.getByRole('link', { name: 'Session Details' }).first()).toBeVisible();

    // Click Filter button
    await page.getByRole('button', { name: /Filter/ }).click();
    await expect(page.getByText('Where')).toBeVisible();

    // Select Chatbot column
    await page.locator('label').filter({ hasText: 'Select column' }).click();
    await page.locator('a').filter({ hasText: /^Chatbot$/ }).click();

    // Verify "any of" operator is shown (use first() to avoid strict mode violation)
    await expect(page.getByText('any of').first()).toBeVisible();

    // Open value selector and select "Customer Support Bot"
    await page.locator('.relative > .w-40').click();
    await page.getByRole('checkbox', { name: 'Customer Support Bot' }).check();

    // Wait for filter to be applied
    await expect(page).toHaveURL(/filter_0_column=experiment/);

    // Verify Filter (1) indicator appears
    await expect(page.getByRole('button', { name: /Filter \(1\)/ })).toBeVisible();

    // Wait for filtered table and verify sessions from Customer Support Bot are shown
    const filteredTable = page.getByRole('table');
    await expect(filteredTable).toBeVisible({ timeout: 10000 });
    await expect(filteredTable.getByRole('link', { name: 'Customer Support Bot' }).first()).toBeVisible({ timeout: 10000 });
  });

  test('View Chatbot Sessions tab', async ({ page }) => {
    // Navigate to Chatbots
    await page.goto('/a/agent/chatbots/');
    await expect(page).toHaveURL(/\/chatbots\/$/);


    // Wait for table to load and click on Customer Support Bot
    const chatbotsTable = page.getByRole('table');
    await expect(chatbotsTable).toBeVisible({ timeout: 10000 });
    await page.getByRole('link', { name: 'Customer Support Bot' }).click();


    // Verify we are on the chatbot details page
    await expect(page.getByRole('heading', { name: 'Customer Support Bot' })).toBeVisible();

    // Verify Sessions tab is visible and active
    await expect(page.getByRole('tab', { name: 'Sessions' })).toBeVisible();

    // Verify sessions table is visible with expected columns
    const sessionsTable = page.getByRole('table');
    await expect(sessionsTable).toBeVisible({ timeout: 10000 });
    await expect(sessionsTable.getByRole('columnheader', { name: 'Participant' })).toBeVisible();
    await expect(sessionsTable.getByRole('columnheader', { name: 'Message Count' })).toBeVisible();
    await expect(sessionsTable.getByRole('columnheader', { name: 'State' })).toBeVisible();
    await expect(sessionsTable.getByRole('columnheader', { name: 'Actions' })).toBeVisible();

    // Verify Filter and Date Range controls
    await expect(page.getByRole('button', { name: /Filter/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Date Range/ })).toBeVisible();

    // Verify Generate Chat Export button
    await expect(page.getByRole('button', { name: /Generate Chat Export/ })).toBeVisible();

    // Verify at least one session row exists
    await expect(sessionsTable.getByRole('link', { name: 'Session Details' }).first()).toBeVisible();
  });

  test('Filter Chatbot Sessions by Status (active)', async ({ page }) => {
    // Navigate to Customer Support Bot
    await page.goto('/a/agent/chatbots/');

    const chatbotsTable = page.getByRole('table');
    await expect(chatbotsTable).toBeVisible({ timeout: 10000 });
    await page.getByRole('link', { name: 'Customer Support Bot' }).click();

    await expect(page.getByRole('heading', { name: 'Customer Support Bot' })).toBeVisible();

    // Wait for sessions table
    const sessionsTable = page.getByRole('table');
    await expect(sessionsTable.getByRole('link', { name: 'Session Details' }).first()).toBeVisible({ timeout: 10000 });

    // Click Filter button
    await page.getByRole('button', { name: /Filter/ }).click();
    await expect(page.getByText('Where')).toBeVisible();

    // Select Status column
    await page.locator('label').filter({ hasText: 'Select column' }).click();
    await page.getByText('Status', { exact: true }).click();

    // Open value selector and select "active"
    await page.locator('.relative > .w-40').click();
    await page.getByRole('checkbox', { name: 'active' }).check();

    // Wait for filter to be applied
    await expect(page).toHaveURL(/filter_0_column=state/);
    await expect(page).toHaveURL(/filter_0_value=.*active/);

    // Verify Filter (1) indicator and filter params are applied
    await expect(page.getByRole('button', { name: /Filter \(1\)/ })).toBeVisible();
  });

  test('View Session Details with metadata and tabs', async ({ page }) => {
    // Navigate to All sessions
    await page.getByRole('link', { name: 'All sessions' }).click();
    await expect(page).toHaveURL(/\/chatbots\/sessions\/$/);


    // Wait for table to load
    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Click the first Session Details link
    await table.getByRole('link', { name: 'Session Details' }).first().click();


    // Verify session detail page elements
    await expect(page.getByRole('heading', { name: 'Chatbot Review' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Session details' })).toBeVisible();

    // Verify session metadata
    await expect(page.getByText('Participant', { exact: true })).toBeVisible();
    await expect(page.getByText('Status')).toBeVisible();
    await expect(page.getByText('Started')).toBeVisible();
    await expect(page.getByText('Chatbot', { exact: true })).toBeVisible();
    await expect(page.getByText('Platform')).toBeVisible();

    // Verify navigation buttons
    await expect(page.getByRole('link', { name: /Older/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /Newer/ })).toBeVisible();

    // Verify tabs
    await expect(page.getByRole('tab', { name: 'Messages' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Participant Data' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Participant Schedules' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Session State' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Chatbot Events' })).toBeVisible();
  });

  test('Navigate between sessions using Older/Newer buttons', async ({ page }) => {
    // Navigate to All sessions
    await page.getByRole('link', { name: 'All sessions' }).click();

    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Click the first Session Details link
    await table.getByRole('link', { name: 'Session Details' }).first().click();

    await expect(page.getByRole('heading', { name: 'Chatbot Review' })).toBeVisible();

    // Record the current URL
    const firstUrl = page.url();

    // Click Older to navigate to the previous session
    await page.getByRole('link', { name: /Older/ }).click();

    await expect(page.getByRole('heading', { name: 'Chatbot Review' })).toBeVisible();

    // Verify URL changed
    expect(page.url()).not.toBe(firstUrl);

    // Click Newer to go back
    await page.getByRole('link', { name: /Newer/ }).click();

    await expect(page.getByRole('heading', { name: 'Chatbot Review' })).toBeVisible();

    // Verify we are back to the original session
    expect(page.url()).toBe(firstUrl);
  });

  test('Click through session detail tabs', async ({ page }) => {
    // Navigate to All sessions
    await page.getByRole('link', { name: 'All sessions' }).click();

    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Click the first Session Details link
    await table.getByRole('link', { name: 'Session Details' }).first().click();

    await expect(page.getByRole('heading', { name: 'Chatbot Review' })).toBeVisible();

    // Click Participant Data tab
    await page.getByRole('tab', { name: 'Participant Data' }).click();
    await expect(page.getByRole('tab', { name: 'Participant Data' })).toBeVisible();

    // Click Participant Schedules tab
    await page.getByRole('tab', { name: 'Participant Schedules' }).click();
    await expect(page.getByRole('tab', { name: 'Participant Schedules' })).toBeVisible();

    // Click Session State tab
    await page.getByRole('tab', { name: 'Session State' }).click();
    await expect(page.getByRole('tab', { name: 'Session State' })).toBeVisible();

    // Click Chatbot Events tab
    await page.getByRole('tab', { name: 'Chatbot Events' }).click();
    await expect(page.getByRole('tab', { name: 'Chatbot Events' })).toBeVisible();

    // Click Messages tab to go back
    await page.getByRole('tab', { name: 'Messages' }).click();
    await expect(page.getByRole('tab', { name: 'Messages' })).toBeVisible();
  });

  test('End a Session from session detail page', async ({ page }) => {
    // Navigate to All sessions
    await page.getByRole('link', { name: 'All sessions' }).click();

    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Find any endable session (Active or Setting Up); bootstrap sessions may vary in state
    const activeRow = table.getByRole('row').filter({ hasText: /\b(Active|Setting Up)\b/ }).first();
    await expect(activeRow).toBeVisible({ timeout: 10000 });
    await activeRow.getByRole('link', { name: 'Session Details' }).click();

    await expect(page.getByRole('heading', { name: 'Chatbot Review' })).toBeVisible();

    // Click End Session button
    await page.getByRole('button', { name: 'End Session' }).click();

    // Verify confirmation dialog appears
    await expect(page.getByRole('heading', { name: 'End Session?' })).toBeVisible();
    await expect(page.getByText('Are you sure?')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();

    // Confirm ending the session
    await page.locator('#end_session_modal').getByRole('button', { name: 'End Session' }).click();

    // Verify session status changed (End Session button should no longer be visible)
    await expect(page.getByRole('button', { name: 'End Session' })).not.toBeVisible({ timeout: 10000 });
  });

  test('Start New Session from session detail page', async ({ page }) => {
    // Navigate to All sessions
    await page.getByRole('link', { name: 'All sessions' }).click();

    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 10000 });

    // Use Carol Williams (api-platform participant) - "New Session" button only appears for non-web sessions
    await table.getByRole('row', { name: /Carol Williams/ }).getByRole('link', { name: 'Session Details' }).click();

    await expect(page.getByRole('heading', { name: 'Chatbot Review' })).toBeVisible();

    // Click New Session button
    await page.getByRole('button', { name: 'New Session' }).click();

    // Verify the New Session dialog appears
    await expect(page.getByRole('heading', { name: 'Start New Session?' })).toBeVisible();
    await expect(page.getByText('This will end the current session and create a new one.')).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Enter a prompt to send to the' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start New Session' })).toBeVisible();

    // Cancel the dialog to avoid side effects
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('heading', { name: 'Start New Session?' })).not.toBeVisible();
  });

  test.fixme('Generate Chat Export from chatbot sessions tab', async ({ page }) => {
    // FIXME: This test relies on a Celery background task completing to generate the export.
    // In the test environment, the Celery worker may not process the task within the 60s timeout,
    // causing the #chat-exports a[href] link to never appear. The "Generating" state is observed
    // but the export never completes within the allotted time.
    // Navigate to Customer Support Bot
    await page.goto('/a/agent/chatbots/');

    const chatbotsTable = page.getByRole('table');
    await expect(chatbotsTable).toBeVisible({ timeout: 10000 });
    await page.getByRole('link', { name: 'Customer Support Bot' }).click();

    await expect(page.getByRole('heading', { name: 'Customer Support Bot' })).toBeVisible();

    // Wait for Sessions tab to load
    await expect(page.getByRole('tab', { name: 'Sessions' })).toBeVisible();

    // Click Generate Chat Export
    await page.getByRole('button', { name: /Generate Chat Export/ }).click();

    // Verify download link appears after export is generated
    // Allow up to 60s for the Celery task to complete and HTMX to poll and render the link
    // The template renders the link with text "Download export" inside #chat-exports
    await expect(page.locator('#chat-exports a[href]').first()).toBeVisible({ timeout: 60000 });
  });
});
