import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Collections', () => {
  test('Create a Media Collection', async ({ page }) => {
    await login(page);

    // Navigate to Collections
    await page.getByRole('link', { name: 'Collections' }).click();
    await expect(page).toHaveURL(/\/documents\/collection\/$/);
    await expect(page.getByRole('heading', { name: 'Collections' })).toBeVisible();

    // Click "Add new"
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page).toHaveURL(/\/documents\/collection\/new\/$/);
    await expect(page.getByRole('heading', { name: 'Create Collection' })).toBeVisible();

    // Select "Media Collection"
    await page.locator('label').filter({ hasText: 'Media Collection Share files' }).click();

    // Fill in the Name
    const collectionName = `Media Collection ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(collectionName);

    // Click "Create"
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect to collection detail page
    await expect(page).toHaveURL(/\/documents\/collections\/\d+$/);
  });

  test('Create an Indexed Collection (RAG)', async ({ page }) => {
    await login(page);

    // Navigate to Collections
    await page.getByRole('link', { name: 'Collections' }).click();
    await expect(page).toHaveURL(/\/documents\/collection\/$/);
    await expect(page.getByRole('heading', { name: 'Collections' })).toBeVisible();

    // Click "Add new"
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page).toHaveURL(/\/documents\/collection\/new\/$/);
    await expect(page.getByRole('heading', { name: 'Create Collection' })).toBeVisible();

    // Wait for Alpine.js to initialize the collection component, then set isIndex = true
    await page.waitForFunction(() => {
      const el = document.querySelector('[x-data="collection"]') as any;
      const Alpine = (window as any).Alpine;
      if (!el || !Alpine) return false;
      const data = Alpine.$data(el);
      return data && typeof data.isIndex !== 'undefined';
    }, { timeout: 10000 });
    await page.evaluate(() => {
      const el = document.querySelector('[x-data="collection"]') as any;
      (window as any).Alpine.$data(el).isIndex = true;
    });

    // Wait for the LLM Provider field to become visible (driven by Alpine.js isIndex = true)
    await expect(page.locator('#id_llm_provider')).toBeVisible({ timeout: 10000 });

    // Fill in the Name
    const collectionName = `Indexed Collection ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(collectionName);

    // Select LLM Provider (using ID selector since field is inside x-show container)
    await page.locator('#id_llm_provider').selectOption({ label: 'OpenAI: OpenAI' });

    // Select Embedding provider model
    await page.locator('#id_embedding_provider_model').selectOption('text-embedding-3-small');

    // Click "Create"
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect to collection detail page
    await expect(page).toHaveURL(/\/documents\/collections\/\d+$/);
  });
});
