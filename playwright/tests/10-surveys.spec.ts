import { test, expect } from '@playwright/test';
import { login } from '../helpers/common';

test.describe('Surveys', () => {
  test('Create a survey', async ({ page }) => {
    // Sign in
    await login(page);

    // Navigate to Surveys
    await page.getByRole('link', { name: 'Surveys' }).click();
    await expect(page).toHaveURL(/\/experiments\/survey\/$/);
    await expect(page.getByRole('heading', { name: 'Survey' })).toBeVisible();

    // Click "Add new"
    await page.getByRole('link', { name: 'Add new' }).click();
    await expect(page).toHaveURL(/\/experiments\/survey\/new\/$/);
    await expect(page.getByRole('heading', { name: 'Create Survey' })).toBeVisible();

    // Fill in the survey name
    const surveyName = `Test Survey ${Date.now()}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(surveyName);

    // Fill in the survey URL
    const surveyUrl = 'https://example.com/survey';
    await page.getByRole('textbox', { name: 'Url' }).fill(surveyUrl);

    // Verify the User Message template is pre-filled with the {survey_link} placeholder
    const userMessage = page.getByRole('textbox', { name: 'User Message' });
    await expect(userMessage).toHaveValue(/{survey_link}/);

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect back to survey list and survey appears
    await expect(page).toHaveURL(/\/experiments\/survey\/$/);
    const surveyRow = page.getByRole('row', { name: new RegExp(surveyName) });
    await expect(surveyRow.getByRole('cell', { name: surveyName })).toBeVisible();
    // Scope link check to this row to avoid strict mode violations from prior runs with same URL
    await expect(surveyRow.getByRole('link', { name: surveyUrl })).toBeVisible();
  });
});
