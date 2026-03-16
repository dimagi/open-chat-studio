import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Source Material', () => {
  test('Create source material', async ({ page }) => {
    // Sign in
    await login(page);

    // Navigate to Source Material
    await page.getByRole('link', { name: 'Source Material' }).click();
    await expect(page).toHaveURL(/\/experiments\/source_material\/$/);
    await expect(page.getByRole('heading', { name: 'Source Material' })).toBeVisible();

    // Click "Add new"
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page).toHaveURL(/\/experiments\/source_material\/new\/$/);
    await expect(page.getByRole('heading', { name: 'Create Source Material' })).toBeVisible();

    // Fill in the form
    const topicName = `Test Source Material ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Topic' }).fill(topicName);
    await page.getByRole('textbox', { name: 'A longer description of the' }).fill('This is a test description for source material');
    await page.getByRole('textbox', { name: 'Material', exact: true }).fill('This is the material content for testing purposes.');

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect back to source material list and entry appears
    await expect(page).toHaveURL(/\/experiments\/source_material\/$/);
    await expect(page.getByRole('cell', { name: topicName })).toBeVisible();
  });

  test('Search source material', async ({ page }) => {
    // Sign in
    await login(page);

    // Navigate to Source Material
    await page.getByRole('link', { name: 'Source Material' }).click();
    await expect(page).toHaveURL(/\/experiments\/source_material\/$/);
    await expect(page.getByRole('heading', { name: 'Source Material' })).toBeVisible();

    // Search for a known topic name
    const searchBox = page.getByRole('searchbox', { name: 'Search...' });
    await searchBox.fill('Test Source Material');
    await searchBox.press('Enter');

    // Verify matching source material is shown in the table
    await expect(page.getByRole('cell', { name: 'Test Source Material' }).first()).toBeVisible();

    // Clear the search box, type a non-existent term and press Enter
    await searchBox.fill('nonexistent material');
    await searchBox.press('Enter');

    // Verify the table shows "No source material found."
    await expect(page.getByRole('cell', { name: 'No source material found.' })).toBeVisible();
  });
});
