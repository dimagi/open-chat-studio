import { test, expect, Page } from '@playwright/test';
import { EMAIL, PASSWORD } from '../helpers/common';

async function login(page: Page, email: string, password: string) {
  await page.goto('/accounts/login/');
  await page.getByRole('textbox', { name: 'Email' }).fill(email);
  await page.getByRole('textbox', { name: 'Password' }).fill(password);
  await page.getByRole('button', { name: 'Sign In' }).click();
}

test.describe('Authentication', () => {
  test('Sign in and verify redirect to dashboard', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: /Build Chatbots/i })).toBeVisible();

    await page.goto('/accounts/login/');
    await expect(page.getByRole('heading', { name: 'Sign In' })).toBeVisible();

    await page.getByRole('textbox', { name: 'Email' }).fill(EMAIL);
    await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign In' }).click();

    await expect(page).toHaveURL(/\/a\/\w+\/dashboard\//);
    await expect(page.getByRole('heading', { name: 'Team Dashboard' })).toBeVisible();
  });

  test('Sign out and verify redirect to landing page', async ({ page }) => {
    await login(page, EMAIL, PASSWORD);
    await expect(page).toHaveURL(/\/dashboard\//);

    await page.getByRole('link', { name: 'Sign out' }).click();

    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: /Build Chatbots/i })).toBeVisible();
  });
});
