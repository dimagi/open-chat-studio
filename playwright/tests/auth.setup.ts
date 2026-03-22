import { test as setup } from '@playwright/test';
import path from 'path';

const authFile = path.join(__dirname, '../.auth/user.json');

setup('authenticate', async ({ page }) => {
  await page.goto('/accounts/login/');
  await page.getByRole('textbox', { name: 'Email' }).fill('tester@playwright.com');
  await page.getByLabel('Password').fill('My0riginalP@ssw0rd!');
  await page.getByRole('button', { name: 'Sign In' }).click();
  await page.waitForURL(/\/dashboard\//);
  await page.context().storageState({ path: authFile });
});
