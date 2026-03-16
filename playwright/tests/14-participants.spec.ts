import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Participants', () => {
  test('View Participants page and verify table', async ({ page }) => {
    await login(page);

    // Navigate to Participants in the sidebar
    await page.getByRole('link', { name: 'Participants' }).click();
    await expect(page).toHaveURL(/\/participants\/participant\/$/);

    await expect(page.getByRole('heading', { name: 'Participants' })).toBeVisible();

    // Verify the participants table is visible with expected columns
    await expect(page.getByRole('columnheader', { name: 'Name' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Channel' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Identifier' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Created On' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Remote id' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Actions' })).toBeVisible();

    // Verify Filter and Date Range controls are present
    await expect(page.getByRole('button', { name: /Filter/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Date Range/ })).toBeVisible();
  });

  test('Use Filter controls', async ({ page }) => {
    await login(page);

    // Navigate to Participants
    await page.getByRole('link', { name: 'Participants' }).click();
    await expect(page).toHaveURL(/\/participants\/participant\/$/);

    // Wait for the participants table to fully load before clicking Filter
    await page.waitForSelector('table tbody tr', { timeout: 10000 });

    // Click Filter button to open filter panel
    await page.getByRole('button', { name: /Filter/ }).click();

    // Verify the filter panel opens with column selector
    // The panel uses Alpine.js x-show + x-cloak; wait for it to become visible
    await expect(page.getByText('Where')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Select column')).toBeVisible({ timeout: 15000 });

    // Open the column selector dropdown; options are <a> elements without href (generic role)
    await page.getByText('Select column').click();

    // Verify filter column options are available via DOM (DaisyUI CSS dropdowns lose focus
    // when Playwright runs visibility assertions, so read options as DOM text directly)
    const columnOptions = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('ul.dropdown-content a'))
        .map(a => (a as HTMLElement).textContent?.trim())
        .filter(Boolean);
    });
    expect(columnOptions).toContain('Created On');
    expect(columnOptions).toContain('Name/Identifier');
    expect(columnOptions).toContain('Remote ID');
    expect(columnOptions).toContain('Channels');

    // Select Name/Identifier by clicking within the dropdown list (options are <a> without href)
    await page.locator('ul.dropdown-content').getByText('Name/Identifier').click();

    // Verify the filter input appears
    await expect(page.getByRole('textbox', { name: 'Enter value...' })).toBeVisible();
  });

  test('Use Date Range controls', async ({ page }) => {
    await login(page);

    // Navigate to Participants
    await page.getByRole('link', { name: 'Participants' }).click();
    await expect(page).toHaveURL(/\/participants\/participant\/$/);


    // Click Date Range button
    await page.getByRole('button', { name: /Date Range/ }).click();

    // Verify date range options appear
    await expect(page.getByRole('link', { name: 'Last 1 Hour' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Last 1 Day' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Last 7 Days' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Last 14 Days' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Last 30 Days' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Last 3 Months' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Last Year' })).toBeVisible();

    // Select Last 7 Days
    await page.getByRole('link', { name: 'Last 7 Days' }).click();

    // Verify the date range filter is applied (URL updates and button text changes)
    await expect(page).toHaveURL(/filter_0_column=created_on/);
    await expect(page).toHaveURL(/filter_0_value=7d/);
    await expect(page.getByRole('button', { name: /Last 7 Days/ })).toBeVisible();
  });

  test('Filter by chatbot using dropdown', async ({ page }) => {
    await login(page);

    // Navigate to Participants
    await page.getByRole('link', { name: 'Participants' }).click();
    await expect(page).toHaveURL(/\/participants\/participant\/$/);


    // Select a chatbot from the dropdown (hidden select, use JS)
    await page.evaluate(() => {
      const select = document.querySelector('#id_experiment') as HTMLSelectElement;
      select.value = select.options[1].value; // Select first chatbot
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    // Verify the chatbot is selected
    const selectedValue = await page.evaluate(() => {
      const select = document.querySelector('#id_experiment') as HTMLSelectElement;
      return select.options[select.selectedIndex].text;
    });
    expect(selectedValue).not.toBe('---------');
  });

  test('Export participants with chatbot selected', async ({ page }) => {
    await login(page);

    // Navigate to Participants
    await page.getByRole('link', { name: 'Participants' }).click();
    await expect(page).toHaveURL(/\/participants\/participant\/$/);


    // Select a chatbot from the dropdown
    await page.evaluate(() => {
      const select = document.querySelector('#id_experiment') as HTMLSelectElement;
      select.value = select.options[1].value; // Select first chatbot
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    // Click Export button (using JS to avoid debug toolbar interference)
    await page.evaluate(() => {
      const btn = document.querySelector('button[id*="action-"]') as HTMLButtonElement;
      btn.click();
    });

    // Verify the export modal opens
    await expect(page.getByRole('heading', { name: 'Export Participant Data' })).toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Chatbot' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Export data' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close' })).toBeVisible();

    // Verify explanatory text is shown
    await expect(page.getByText('Selecting a chatbot will filter the export')).toBeVisible();

    // Click Export data and verify file download
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export data' }).click();
    const download = await downloadPromise;

    // Verify the downloaded file is a CSV
    expect(download.suggestedFilename()).toMatch(/participants.*\.csv$/);
  });
});
