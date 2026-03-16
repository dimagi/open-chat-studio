import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('View team dashboard with analytics sections', async ({ page }) => {
    // Verify we are on the dashboard
    await expect(page.getByRole('heading', { name: 'Team Dashboard' })).toBeVisible();
    await expect(page.getByText('Comprehensive analytics for your chatbots')).toBeVisible();

    // Verify summary cards are visible
    await expect(page.getByText('Active Chatbots')).toBeVisible();
    await expect(page.getByText('Active Participants', { exact: true }).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Completed Sessions')).toBeVisible();
    await expect(page.getByText('Total Messages')).toBeVisible();

    // Verify chart sections are visible
    const activeParticipantsChart = page.locator('text=Active Participants').last();
    await expect(activeParticipantsChart).toBeVisible();
    await expect(page.getByText('Active Sessions')).toBeVisible();
    await expect(page.getByText('Message Volume Trends')).toBeVisible();
    await expect(page.getByText('Channel Breakdown')).toBeVisible();
    await expect(page.getByText('Average Response Time')).toBeVisible();
  });

  test('View Bot Performance Summary table', async ({ page }) => {
    await expect(page.getByText('Bot Performance Summary')).toBeVisible();

    // Verify table headers
    const table = page.locator('table').first();
    await expect(table.getByRole('columnheader', { name: 'Chatbot' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: /Participants/ })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: /Sessions/ })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: /Messages/ })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: /Avg Duration/ })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: /Completion Rate/ })).toBeVisible();

    // Verify at least the header row exists (table may have no data rows in test environment)
    const rowCount = await table.getByRole('row').count();
    expect(rowCount).toBeGreaterThanOrEqual(1);
  });

  test('View Most Active Participants and Session Length Distribution', async ({ page }) => {
    await expect(page.getByText('Most Active Participants')).toBeVisible();
    await expect(page.getByText('Session Length Distribution')).toBeVisible();

    // Verify Most Active Participants table headers
    const participantsTable = page.locator('table').nth(1);
    await expect(participantsTable.getByRole('columnheader', { name: 'Participant' })).toBeVisible();
    await expect(participantsTable.getByRole('columnheader', { name: 'Messages' })).toBeVisible();
    await expect(participantsTable.getByRole('columnheader', { name: 'Sessions' })).toBeVisible();
    await expect(participantsTable.getByRole('columnheader', { name: 'Last Activity' })).toBeVisible();
  });

  test('Adjust filters: Date Range', async ({ page }) => {
    // Helper: set a plain <select> value and dispatch bubbling input+change so Alpine.js @change handler fires
    // The date range select has data-filter-type="date_range" and id="id_date_range"
    const setDateRange = async (value: string) => {
      await page.evaluate((val) => {
        // Try by id first, then by data-filter-type attribute
        const el = (document.querySelector('#id_date_range') || document.querySelector('[data-filter-type="date_range"]')) as HTMLSelectElement;
        if (!el) throw new Error('Date range select not found');
        el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }, value);
    };

    // Wait for the dashboard form to be ready (element attached to DOM, may be hidden by TomSelect)
    await page.waitForSelector('#id_date_range, [data-filter-type="date_range"]', { state: 'attached', timeout: 10000 });

    // Select Last 7 days
    await setDateRange('7');
    await expect(page).toHaveURL(/date_range=7/, { timeout: 5000 });

    // Select Last 30 days
    await setDateRange('30');
    await expect(page).toHaveURL(/date_range=30/, { timeout: 5000 });

    // Select Last 3 months
    await setDateRange('90');
    await expect(page).toHaveURL(/date_range=90/, { timeout: 5000 });

    // Select Last year
    await setDateRange('365');
    await expect(page).toHaveURL(/date_range=365/, { timeout: 5000 });

    // Select Custom range
    await setDateRange('custom');
    await expect(page).toHaveURL(/date_range=custom/, { timeout: 5000 });
  });

  test('Adjust filters: Granularity', async ({ page }) => {
    // Helper: set a plain <select> value and dispatch bubbling input+change so Alpine.js @change handler fires
    // The granularity select has data-filter-type="granularity" and id="id_granularity"
    const setGranularity = async (value: string) => {
      await page.evaluate((val) => {
        // Try by id first, then by data-filter-type attribute
        const el = (document.querySelector('#id_granularity') || document.querySelector('[data-filter-type="granularity"]')) as HTMLSelectElement;
        if (!el) throw new Error('Granularity select not found');
        el.value = val;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }, value);
    };

    // Wait for the dashboard form to be ready (element attached to DOM, may be hidden by TomSelect)
    await page.waitForSelector('#id_granularity, [data-filter-type="granularity"]', { state: 'attached', timeout: 10000 });

    // Select Hourly
    await setGranularity('hourly');
    await expect(page).toHaveURL(/granularity=hourly/, { timeout: 5000 });

    // Select Daily
    await setGranularity('daily');
    await expect(page).toHaveURL(/granularity=daily/, { timeout: 5000 });

    // Select Weekly
    await setGranularity('weekly');
    await expect(page).toHaveURL(/granularity=weekly/, { timeout: 5000 });

    // Select Monthly
    await setGranularity('monthly');
    await expect(page).toHaveURL(/granularity=monthly/, { timeout: 5000 });
  });

  test('Adjust filters: Channels, Chatbots, Participants, Tags', async ({ page }) => {
    // Select a channel via Tom Select combobox
    const channelsCombo = page.getByRole('combobox', { name: 'Select channels...' });
    await channelsCombo.click();
    await page.locator('#id_channels-opt-2').click(); // API
    await expect(page).toHaveURL(/channels=api/);

    // Select a chatbot
    const chatbotsCombo = page.getByRole('combobox', { name: 'Select chatbots...' });
    await chatbotsCombo.click();
    await page.locator('#id_experiments-opt-2').click(); // First chatbot
    await expect(page).toHaveURL(/experiments=\d+/);

    // Select a participant
    const participantsCombo = page.getByRole('combobox', { name: 'Select participants...' });
    await participantsCombo.click();
    await page.locator('#id_participants-opt-2').click(); // First participant
    await expect(page).toHaveURL(/participants=\d+/);

    // Select a tag if any exist — skip the first option (blank placeholder), use the second one (first real tag)
    const tagsCombo = page.getByRole('combobox', { name: 'Select tags...' });
    await tagsCombo.click();
    // TomSelect options: index 0 is the blank placeholder, index 1+ are real tags
    // Use nth(1) to skip the placeholder and get the first real tag option
    const firstTagOption = page.locator('[id^="id_tags-opt-"]').nth(1);
    if (await firstTagOption.isVisible({ timeout: 2000 }).catch(() => false)) {
      await firstTagOption.click();
      // Wait for the TomSelect onChange to propagate through Alpine and update the URL
      await page.waitForTimeout(600);
      await expect(page).toHaveURL(/tags=\d+/, { timeout: 5000 });
    }
  });

  test('Save Filters to persist selections', async ({ page }) => {
    // Wait for the dashboard form to be ready before setting filters
    await page.waitForSelector('#id_date_range, [data-filter-type="date_range"]', { state: 'attached', timeout: 10000 });

    // Set some filters first — dispatch bubbling events so Alpine.js @change handler fires
    await page.evaluate(() => {
      const el = (document.querySelector('#id_date_range') || document.querySelector('[data-filter-type="date_range"]')) as HTMLSelectElement;
      if (!el) throw new Error('Date range select not found');
      el.value = '7';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await expect(page).toHaveURL(/date_range=7/, { timeout: 5000 });

    await page.evaluate(() => {
      const el = (document.querySelector('#id_granularity') || document.querySelector('[data-filter-type="granularity"]')) as HTMLSelectElement;
      if (!el) throw new Error('Granularity select not found');
      el.value = 'weekly';
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await expect(page).toHaveURL(/granularity=weekly/, { timeout: 5000 });

    // Click Save Filters
    await page.getByRole('button', { name: /Save Filters/ }).click();

    // Verify the save modal appears
    await expect(page.getByRole('heading', { name: 'Save Filter Preset' })).toBeVisible();
    await expect(page.locator('#filtersModal').getByRole('textbox')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();

    // Fill in preset name and save
    const presetName = `E2E Preset ${Date.now()}`;
    await page.locator('#filtersModal').getByRole('textbox').fill(presetName);
    await page.getByRole('button', { name: 'Save', exact: true }).click();

    // Verify the modal closes (dialog should no longer be visible)
    await expect(page.getByRole('heading', { name: 'Save Filter Preset' })).not.toBeVisible();
  });
});
