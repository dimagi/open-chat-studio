import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Consent Forms', () => {
  test('Create a consent form with default settings', async ({ page }) => {
    await login(page);

    // Navigate to Consent Forms
    await page.getByRole('link', { name: 'Consent Forms' }).click();
    await expect(page).toHaveURL(/\/experiments\/consent\/$/);
    await expect(page.getByRole('heading', { name: 'Consent Forms' })).toBeVisible();

    // Click "Add new"
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page).toHaveURL(/\/experiments\/consent\/new\/$/);
    await expect(page.getByRole('heading', { name: 'Create Consent Form' })).toBeVisible();

    // Fill in the form
    const consentName = `Test Consent ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(consentName);
    await page.getByRole('textbox', { name: 'Consent text' }).fill(
      'By participating in this study, you agree to the terms and conditions.'
    );

    // Verify "Capture identifier" is enabled by default
    await expect(page.getByRole('checkbox', { name: 'Capture identifier' })).toBeChecked();

    // Verify default identifier label
    await expect(page.getByRole('textbox', { name: 'Identifier label' })).toHaveValue('Email Address');

    // Verify default identifier type is Email
    await expect(page.getByRole('combobox', { name: 'Identifier type' })).toHaveValue('email');

    // Verify default confirmation text
    await expect(page.getByRole('textbox', { name: 'Confirmation text' })).toHaveValue(
      "Respond with '1' if you agree"
    );

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect back to consent forms list
    await expect(page).toHaveURL(/\/experiments\/consent\/$/);

    // Verify the new consent form appears in the table
    await expect(page.getByRole('cell', { name: consentName })).toBeVisible();
  });

  test('Create a consent form with custom settings', async ({ page }) => {
    await login(page);

    // Navigate to Create Consent Form page
    await page.getByRole('link', { name: 'Consent Forms' }).click();
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page.getByRole('heading', { name: 'Create Consent Form' })).toBeVisible();

    // Fill in the form with custom values
    const consentName = `Custom Consent ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(consentName);
    await page.getByRole('textbox', { name: 'Consent text' }).fill(
      'Please read and accept the following terms before proceeding.'
    );

    // Set custom identifier label
    await page.getByRole('textbox', { name: 'Identifier label' }).fill('Full Name');

    // Change identifier type to Text
    await page.getByRole('combobox', { name: 'Identifier type' }).selectOption('text');

    // Set custom confirmation text
    await page.getByRole('textbox', { name: 'Confirmation text' }).fill(
      "Type 'yes' to confirm your consent"
    );

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect and the form appears in the table
    await expect(page).toHaveURL(/\/experiments\/consent\/$/);
    await expect(page.getByRole('cell', { name: consentName })).toBeVisible();

    // Verify the identifier label and type are shown correctly
    const row = page.getByRole('row', { name: new RegExp(consentName) });
    await expect(row.getByRole('cell', { name: 'Full Name' })).toBeVisible();
    await expect(row.getByRole('cell', { name: 'Text' })).toBeVisible();
  });

  test('Create a consent form with capture identifier disabled', async ({ page }) => {
    await login(page);

    // Navigate to Create Consent Form page
    await page.getByRole('link', { name: 'Consent Forms' }).click();
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page.getByRole('heading', { name: 'Create Consent Form' })).toBeVisible();

    // Fill in the form
    const consentName = `No Identifier Consent ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(consentName);
    await page.getByRole('textbox', { name: 'Consent text' }).fill(
      'Anonymous consent form without identifier capture.'
    );

    // Disable "Capture identifier"
    await page.getByRole('checkbox', { name: 'Capture identifier' }).uncheck();
    await expect(page.getByRole('checkbox', { name: 'Capture identifier' })).not.toBeChecked();

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect and the form appears
    await expect(page).toHaveURL(/\/experiments\/consent\/$/);
    await expect(page.getByRole('cell', { name: consentName })).toBeVisible();
  });
});
