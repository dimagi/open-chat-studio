import { test, expect } from '@playwright/test';
import { login, setupPage, deleteActionRow, TEAM_SLUG, TEAM_URL } from '../helpers/common';

const NEW_ACTION_URL = `/a/${TEAM_SLUG}/actions/new/`;

const VALID_API_SCHEMA = JSON.stringify({
  openapi: '3.0.0',
  info: { title: 'Test API', version: '1.0.0' },
  paths: {
    '/test': {
      get: {
        summary: 'Test endpoint',
        operationId: 'getTest',
        responses: { '200': { description: 'OK' } },
      },
    },
  },
});

test.describe('Custom Actions', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page);
    await login(page);
  });

  test('Navigate to Custom Actions section on team page', async ({ page }) => {
    await page.goto(TEAM_URL);
    await expect(page.locator('[data-cy="title-actions"]')).toBeVisible();
    await expect(page.locator('[data-cy="title-actions"]')).toHaveText('Custom Actions');
    // Scope the "Add new" link to the Custom Actions section
    const actionsSection = page.locator('.app-card', { has: page.locator('[data-cy="title-actions"]') });
    await expect(actionsSection.locator('a[data-cy="btn-new"]')).toBeVisible();
  });

  test('Create a custom action', async ({ page }) => {
    const actionName = `Test Action ${Date.now()}`;

    // Navigate to the create custom action page
    await page.goto(NEW_ACTION_URL);
    await expect(page.getByRole('heading', { name: 'Create Custom Action' })).toBeVisible();

    // Fill in required fields
    await page.getByRole('textbox', { name: 'Name' }).fill(actionName);
    await page.getByRole('textbox', { name: 'Description' }).fill('A test custom action for E2E testing');
    await page.getByRole('textbox', { name: 'Base URL' }).fill('https://api.example.com');
    await page.getByRole('textbox', { name: 'API Schema' }).fill(VALID_API_SCHEMA);

    // Submit the form
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify we are redirected to the team page
    await expect(page).toHaveURL(TEAM_URL);

    // Verify the action appears in the Custom Actions table
    await expect(page.getByRole('cell', { name: actionName })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'A test custom action for E2E testing' })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'https://api.example.com' })).toBeVisible();

    // Clean up: delete the created action
    const actionRow = page.getByRole('row', { name: new RegExp(actionName) });
    await deleteActionRow(page, actionRow);
    await expect(page.getByRole('cell', { name: actionName })).not.toBeVisible();
  });

  test('Create a custom action with optional fields', async ({ page }) => {
    const actionName = `Test Action Optional ${Date.now()}`;

    await page.goto(NEW_ACTION_URL);
    await expect(page.getByRole('heading', { name: 'Create Custom Action' })).toBeVisible();

    // Fill in all fields including optional ones
    await page.getByRole('textbox', { name: 'Name' }).fill(actionName);
    await page.getByRole('textbox', { name: 'Description' }).fill('Custom action with optional fields');
    await page.getByRole('textbox', { name: 'Additional Prompt' }).fill('Use this action for testing purposes');
    await page.getByRole('textbox', { name: 'Base URL' }).fill('https://api.example.com');
    await page.getByRole('textbox', { name: 'Health Check Path' }).fill('/health');
    await page.getByRole('textbox', { name: 'API Schema' }).fill(VALID_API_SCHEMA);

    // Submit the form
    await page.getByRole('button', { name: 'Create' }).click();

    // Verify redirect and action appears
    await expect(page).toHaveURL(TEAM_URL);
    await expect(page.getByRole('cell', { name: actionName })).toBeVisible();

    // Clean up: delete the created action
    const actionRow = page.getByRole('row', { name: new RegExp(actionName) });
    await deleteActionRow(page, actionRow);
    await expect(page.getByRole('cell', { name: actionName })).not.toBeVisible();
  });

  test('Validation error when API schema is empty', async ({ page }) => {
    await page.goto(NEW_ACTION_URL);
    await expect(page.getByRole('heading', { name: 'Create Custom Action' })).toBeVisible();

    // Fill in fields but leave the API schema empty
    await page.getByRole('textbox', { name: 'Name' }).fill('Invalid Action');
    await page.getByRole('textbox', { name: 'Description' }).fill('Should fail validation');
    await page.getByRole('textbox', { name: 'Base URL' }).fill('https://api.example.com');
    await page.getByRole('textbox', { name: 'API Schema' }).clear();

    // Submit the form
    await page.getByRole('button', { name: 'Create' }).click();

    // The API Schema field has the HTML required attribute, so the browser
    // prevents form submission. Verify we stayed on the same page.
    await expect(page).toHaveURL(NEW_ACTION_URL);

    // Verify the API Schema textarea is invalid via browser validation
    const apiSchemaField = page.getByRole('textbox', { name: 'API Schema' });
    await expect(apiSchemaField).toBeVisible();
    const isInvalid = await apiSchemaField.evaluate(
      (el: HTMLTextAreaElement) => !el.checkValidity()
    );
    expect(isInvalid).toBeTruthy();
  });

  test('Validation error when API schema has no paths', async ({ page }) => {
    await page.goto(NEW_ACTION_URL);
    await expect(page.getByRole('heading', { name: 'Create Custom Action' })).toBeVisible();

    const schemaWithNoPaths = JSON.stringify({
      openapi: '3.0.0',
      info: { title: 'Test API', version: '1.0.0' },
      paths: {},
    });

    await page.getByRole('textbox', { name: 'Name' }).fill('Invalid Action');
    await page.getByRole('textbox', { name: 'Description' }).fill('Should fail - no paths');
    await page.getByRole('textbox', { name: 'Base URL' }).fill('https://api.example.com');
    await page.getByRole('textbox', { name: 'API Schema' }).fill(schemaWithNoPaths);

    // Submit the form
    await page.getByRole('button', { name: 'Create' }).click();

    // Should stay on the same page with a validation error about no paths
    await expect(page).toHaveURL(NEW_ACTION_URL);
    await expect(page.getByText('No paths found in the schema.')).toBeVisible();
  });

  test('Edit an existing custom action', async ({ page }) => {
    const actionName = `Test Action Edit ${Date.now()}`;

    // First create an action
    await page.goto(NEW_ACTION_URL);
    await page.getByRole('textbox', { name: 'Name' }).fill(actionName);
    await page.getByRole('textbox', { name: 'Description' }).fill('Original description');
    await page.getByRole('textbox', { name: 'Base URL' }).fill('https://api.example.com');
    await page.getByRole('textbox', { name: 'API Schema' }).fill(VALID_API_SCHEMA);
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page).toHaveURL(TEAM_URL);

    // Click on the action name to go to edit page
    await page.getByRole('link', { name: actionName }).click();
    await expect(page.getByRole('heading', { name: 'Update Custom Action' })).toBeVisible();

    // Update the description
    await page.getByRole('textbox', { name: 'Description' }).fill('Updated description');
    await page.getByRole('button', { name: 'Update' }).click();

    // Verify redirect back to team page and updated description
    await expect(page).toHaveURL(TEAM_URL);
    await expect(page.getByRole('cell', { name: 'Updated description' })).toBeVisible();

    // Clean up: delete the created action
    const actionRow = page.getByRole('row', { name: new RegExp(actionName) });
    await deleteActionRow(page, actionRow);
    await expect(page.getByRole('cell', { name: actionName })).not.toBeVisible();
  });
});
