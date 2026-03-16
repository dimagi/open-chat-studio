import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Tags', () => {
  test('Create a tag via Manage Tags', async ({ page }) => {
    // Sign in
    await login(page);

    // Navigate to Manage Tags
    await page.getByRole('link', { name: 'Manage Tags' }).click();
    await expect(page).toHaveURL(/\/annotations\/tag\/$/);
    await expect(page.getByRole('heading', { name: 'Tags' })).toBeVisible();

    // Click "Add new"
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page).toHaveURL(/\/annotations\/tag\/new\/$/);
    await expect(page.getByRole('heading', { name: 'Create Tag' })).toBeVisible();

    // Fill in the tag name
    const tagName = `Test Tag ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(tagName);

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect back to tag list and tag appears
    await expect(page).toHaveURL(/\/annotations\/tag\/$/);
    await expect(page.getByRole('cell', { name: tagName })).toBeVisible();
  });
});
