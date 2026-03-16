import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Profile & Account', () => {
  test('Update profile first name and last name', async ({ page }) => {
    await login(page);

    // Navigate to Profile
    await page.goto('/users/profile/');

    await expect(page.getByRole('heading', { name: 'My Details' })).toBeVisible();

    // Verify Change Picture button is visible
    await expect(page.getByText('Change Picture')).toBeVisible();

    // Update First name and Last name
    const timestamp = Date.now();
    const newFirstName = `First${timestamp}`;
    const newLastName = `Last${timestamp}`;

    await page.getByRole('textbox', { name: 'First name' }).fill(newFirstName);
    await page.getByRole('textbox', { name: 'Last name' }).fill(newLastName);



    // Click Save
    await page.getByRole('button', { name: 'Save', exact: true }).click();

    // Verify we stay on profile page and values are saved
    await expect(page).toHaveURL(/\/users\/profile\//);
    await expect(page.getByRole('textbox', { name: 'First name' })).toHaveValue(newFirstName);
    await expect(page.getByRole('textbox', { name: 'Last name' })).toHaveValue(newLastName);

    // Revert the changes
    await page.getByRole('textbox', { name: 'First name' }).fill('Test');
    await page.getByRole('textbox', { name: 'Last name' }).fill('User');

    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByRole('textbox', { name: 'First name' })).toHaveValue('Test');
  });

  test('Configure notification preferences', async ({ page }) => {
    await login(page);

    // Navigate to Profile
    await page.goto('/users/profile/');

    await expect(page.getByRole('heading', { name: 'Notification Preferences' })).toBeVisible();

    // Configure In-App Notifications: ensure enabled, set level to Warning
    const inAppCheckbox = page.getByRole('checkbox', { name: 'In app enabled' });
    if (!(await inAppCheckbox.isChecked())) {
      await inAppCheckbox.check();
    }
    await page.locator('#id_in_app_level_1').click(); // Warning

    // Configure Email Notifications: enable and set level to Error
    const emailCheckbox = page.getByRole('checkbox', { name: 'Email enabled' });
    if (!(await emailCheckbox.isChecked())) {
      await emailCheckbox.check();
    }
    await page.locator('#id_email_level_2').click(); // Error

    // Click Save Preferences
    await page.getByRole('button', { name: 'Save Preferences' }).click();

    // Verify we stay on profile page
    await expect(page).toHaveURL(/\/users\/profile\//);

    // Verify saved values persist after page reload
    await page.goto('/users/profile/');

    await expect(page.getByRole('checkbox', { name: 'In app enabled' })).toBeChecked();
    await expect(page.locator('#id_in_app_level_1')).toBeChecked(); // Warning
    await expect(page.getByRole('checkbox', { name: 'Email enabled' })).toBeChecked();
    await expect(page.locator('#id_email_level_2')).toBeChecked(); // Error

    // Revert: set In-App back to Info, disable Email, set Email back to Warning

    await page.locator('#id_in_app_level_0').click(); // Info
    await page.getByRole('checkbox', { name: 'Email enabled' }).uncheck();
    await page.locator('#id_email_level_1').click(); // Warning
    await page.getByRole('button', { name: 'Save Preferences' }).click();
    await expect(page).toHaveURL(/\/users\/profile\//);
  });

  test('Create an API key', async ({ page }) => {
    await login(page);

    // Navigate to Profile
    await page.goto('/users/profile/');

    await expect(page.getByRole('heading', { name: 'API Keys' })).toBeVisible();

    // Click New API Key
    await page.getByRole('button', { name: 'New API Key' }).click();

    // Dialog should appear
    await expect(page.getByRole('heading', { name: 'Create a new API Key' })).toBeVisible();

    // Fill in the key name
    const keyName = `E2E Test Key ${Date.now()}`;
    await page.locator('#api_key_modal').getByRole('textbox', { name: 'Name' }).fill(keyName);

    // Click Create Key
    await page.getByRole('button', { name: 'Create Key' }).click();

    // Verify the API key was created - success message with the key value
    await expect(page.getByText('API Key created')).toBeVisible();

    // Verify the key appears in the table
    await expect(page.getByRole('cell', { name: keyName })).toBeVisible();

    // Revoke the key to clean up

    await page.getByRole('button', { name: 'Revoke' }).first().click();

    // Handle confirmation dialog if present
    page.once('dialog', (dialog) => dialog.accept());
  });
});
