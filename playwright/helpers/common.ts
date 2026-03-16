import { expect, Page, Locator } from '@playwright/test';

export const EMAIL = 'tester@playwright.com';
export const PASSWORD = 'My0riginalP@ssw0rd!';
export const TEAM_SLUG = 'agent';
export const TEAM_URL = `/a/${TEAM_SLUG}/team/`;

export async function login(page: Page) {
  await page.goto('/accounts/login/');
  // If storageState is loaded, we're already redirected to dashboard
  if (!page.url().includes('/accounts/login')) return;
  await page.getByRole('textbox', { name: 'Email' }).fill(EMAIL);
  await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign In' }).click();
  await expect(page).toHaveURL(/\/dashboard\//);
}

export async function setupPage(page: Page) {
  await page.addInitScript(() => {
    document.addEventListener('DOMContentLoaded', () => {
      const el = document.getElementById('djDebug');
      if (el) el.style.display = 'none';
    });
  });
}

export async function confirmDeletion(page: Page) {
  // Set up response listener before clicking OK (HTMX won't fire DELETE until onok callback)
  const deleteResp = page.waitForResponse(
    resp => resp.url().includes('/delete/') && resp.request().method() === 'DELETE',
    { timeout: 10000 }
  );
  await page.getByRole('button', { name: 'OK' }).click();
  await deleteResp;
  // HTMX 2.x does not reliably remove rows from the DOM on empty 200 responses.
  // Reload the page to get fresh table data reflecting the deletion.
  await page.reload();
  await page.waitForLoadState('domcontentloaded');
}

export async function deleteActionRow(page: Page, row: Locator) {
  // Custom action delete buttons fire HTMX DELETE directly on click (no alertify confirm).
  // HTMX 2.x does not reliably remove rows from the DOM on empty 200 responses,
  // so we wait for the DELETE response and reload the page.
  const deleteResp = page.waitForResponse(
    resp => resp.url().includes('/delete/') && resp.request().method() === 'DELETE',
    { timeout: 10000 }
  );
  await row.getByRole('button').last().click();
  await deleteResp;
  await page.reload();
  await page.waitForLoadState('domcontentloaded');
}
