import { test, expect } from '@playwright/test';
import { login, setupPage, confirmDeletion, TEAM_SLUG, TEAM_URL } from '../helpers/common';

test.describe('Service Providers', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page);
    await login(page);
  });

  test.describe('LLM Provider', () => {
    const LLM_CREATE_URL = `/a/${TEAM_SLUG}/service_providers/llm/create/`;

    test('Add an LLM provider with default and custom models', async ({ page }) => {
      const providerName = `LLM Provider ${Date.now()}`;

      // Navigate to team page and verify LLM section exists
      await page.goto(TEAM_URL);
      await expect(page.getByRole('heading', { name: 'LLM and Embedding Model Service Providers' })).toBeVisible();

      // Navigate to LLM provider creation page
      await page.goto(LLM_CREATE_URL);
      await expect(page.getByRole('heading', { name: 'LLM Service Provider' })).toBeVisible();

      // Fill in Name
      await page.getByRole('textbox', { name: 'Name' }).fill(providerName);

      // Verify Type defaults to OpenAI
      await expect(page.getByRole('combobox', { name: 'Type' })).toHaveValue('openai');

      // Verify default LLM models are visible
      await expect(page.getByText('Default LLM Models')).toBeVisible();

      // Add a custom LLM model (use unique name to avoid conflicts with prior test runs)
      const customModelName = `my-custom-test-model-${Date.now()}`;
      await page.getByRole('button', { name: '+' }).click();
      await expect(page.getByRole('heading', { name: 'Create a new Custom Model' })).toBeVisible();
      await page.locator('#new_model_form #id_name').fill(customModelName);
      await page.getByRole('button', { name: 'Save' }).click();

      // Verify the custom model appears in the list
      await expect(page.getByText(customModelName)).toBeVisible();

      // Fill in API Key
      await page.getByRole('textbox', { name: /API Key/ }).fill('sk-test-mock-key-12345');

      // Click Create
      await page.getByRole('button', { name: 'Create' }).click();

      // Verify redirect to team page and provider appears
      await expect(page).toHaveURL(TEAM_URL);
      await expect(page.getByRole('cell', { name: providerName })).toBeVisible();

      // Clean up: delete the provider
      const providerRow = page.getByRole('row', { name: new RegExp(providerName) });
      await providerRow.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name: providerName })).not.toBeVisible();
    });

    test('Select a different LLM provider type', async ({ page }) => {
      const providerName = `Anthropic Provider ${Date.now()}`;

      await page.goto(LLM_CREATE_URL);
      await expect(page.getByRole('heading', { name: 'LLM Service Provider' })).toBeVisible();

      // Fill in Name
      await page.getByRole('textbox', { name: 'Name' }).fill(providerName);

      // Change type to Anthropic
      await page.getByRole('combobox', { name: 'Type' }).selectOption('anthropic');

      // Fill in API Key
      await page.getByRole('textbox', { name: /Anthropic API Key/ }).fill('sk-ant-test-mock-key-12345');

      // Click Create
      await page.getByRole('button', { name: 'Create' }).click();

      // Verify redirect to team page and provider appears
      await expect(page).toHaveURL(TEAM_URL);
      const providerRow = page.getByRole('row', { name: new RegExp(providerName) });
      await expect(providerRow.getByRole('cell', { name: providerName })).toBeVisible();
      await expect(providerRow.getByRole('cell', { name: 'Anthropic', exact: true })).toBeVisible();

      // Clean up: delete the provider
      await providerRow.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name: providerName })).not.toBeVisible();
    });

    test('LLM provider with optional fields', async ({ page }) => {
      const providerName = `LLM Optional ${Date.now()}`;

      await page.goto(LLM_CREATE_URL);

      await page.getByRole('textbox', { name: 'Name' }).fill(providerName);
      await page.getByRole('textbox', { name: /API Key/ }).fill('sk-test-mock-key-12345');
      await page.getByRole('textbox', { name: /API Base URL/ }).fill('https://custom-api.example.com/v1');
      await page.getByRole('textbox', { name: /Organization ID/ }).fill('org-test123');

      await page.getByRole('button', { name: 'Create' }).click();

      await expect(page).toHaveURL(TEAM_URL);
      await expect(page.getByRole('cell', { name: providerName })).toBeVisible();

      // Clean up
      const providerRow = page.getByRole('row', { name: new RegExp(providerName) });
      await providerRow.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name: providerName })).not.toBeVisible();
    });
  });

  test.describe('Speech Provider', () => {
    const VOICE_CREATE_URL = `/a/${TEAM_SLUG}/service_providers/voice/create/`;

    test('Add a speech provider', async ({ page }) => {
      const providerName = `Speech Provider ${Date.now()}`;

      // Navigate to team page and verify Speech section exists
      await page.goto(TEAM_URL);
      await expect(page.getByRole('heading', { name: 'Speech Service Providers' })).toBeVisible();

      // Navigate to speech provider creation page
      await page.goto(VOICE_CREATE_URL);
      await expect(page.getByRole('heading', { name: 'Speech Service Provider' })).toBeVisible();

      // Fill in required fields (AWS Polly is default)
      await page.getByRole('textbox', { name: 'Name' }).fill(providerName);
      await expect(page.getByRole('combobox', { name: 'Type' })).toHaveValue('aws');
      await page.getByRole('textbox', { name: 'Access Key ID' }).fill('AKIAIOSFODNN7EXAMPLE');
      await page.getByRole('textbox', { name: 'Secret Access Key' }).fill('wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY');
      await page.getByRole('textbox', { name: 'Region' }).fill('us-east-1');

      // Click Create
      await page.getByRole('button', { name: 'Create' }).click();

      // Verify redirect to team page and provider appears
      await expect(page).toHaveURL(TEAM_URL);
      const providerRow = page.getByRole('row', { name: new RegExp(providerName) });
      await expect(providerRow.getByRole('cell', { name: providerName })).toBeVisible();
      await expect(providerRow.getByRole('cell', { name: 'AWS Polly' })).toBeVisible();

      // Clean up: delete the provider
      await providerRow.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name: providerName })).not.toBeVisible();
    });
  });

  test.describe('Messaging Provider', () => {
    const MESSAGING_CREATE_URL = `/a/${TEAM_SLUG}/service_providers/messaging/create/`;

    test('Add a messaging provider', async ({ page }) => {
      const providerName = `Messaging Provider ${Date.now()}`;

      // Navigate to team page and verify Messaging section exists
      await page.goto(TEAM_URL);
      await expect(page.getByRole('heading', { name: 'Messaging Providers' })).toBeVisible();

      // Navigate to messaging provider creation page
      await page.goto(MESSAGING_CREATE_URL);
      await expect(page.getByRole('heading', { name: 'Messaging Provider' })).toBeVisible();

      // Fill in required fields (Twilio is default)
      await page.getByRole('textbox', { name: 'Name' }).fill(providerName);
      await expect(page.getByRole('combobox', { name: 'Type' })).toHaveValue('twilio');
      await page.getByRole('textbox', { name: 'Account SID' }).fill('twilio-mock-secret');
      await page.getByRole('textbox', { name: /Auth Token/ }).fill('mock-auth-token-12345');

      // Click Create
      await page.getByRole('button', { name: 'Create' }).click();

      // Verify redirect to team page and provider appears
      await expect(page).toHaveURL(TEAM_URL);
      const providerRow = page.getByRole('row', { name: new RegExp(providerName) });
      await expect(providerRow.getByRole('cell', { name: providerName })).toBeVisible();
      await expect(providerRow.getByRole('cell', { name: 'Twilio' })).toBeVisible();

      // Clean up: delete the provider
      await providerRow.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name: providerName })).not.toBeVisible();
    });
  });

  test.describe('Authentication Provider', () => {
    const AUTH_CREATE_URL = `/a/${TEAM_SLUG}/service_providers/auth/create/`;

    test('Add an authentication provider', async ({ page }) => {
      const providerName = `Auth Provider ${Date.now()}`;

      // Navigate to team page and verify Authentication section exists
      await page.goto(TEAM_URL);
      await expect(page.getByRole('heading', { name: 'Authentication Providers' })).toBeVisible();

      // Navigate to auth provider creation page
      await page.goto(AUTH_CREATE_URL);
      await expect(page.getByRole('heading', { name: 'Authentication Provider' })).toBeVisible();

      // Fill in required fields (Basic is default)
      await page.getByRole('textbox', { name: 'Name', exact: true }).fill(providerName);
      await page.getByRole('textbox', { name: /Username/ }).fill('testuser');
      await page.getByRole('textbox', { name: 'Password' }).fill('testpass123');

      // Click Create
      await page.getByRole('button', { name: 'Create' }).click();

      // Verify redirect to team page and provider appears
      await expect(page).toHaveURL(TEAM_URL);
      const providerRow = page.getByRole('row', { name: new RegExp(providerName) });
      await expect(providerRow.getByRole('cell', { name: providerName })).toBeVisible();
      await expect(providerRow.getByRole('cell', { name: 'Basic' })).toBeVisible();

      // Clean up: delete the provider
      await providerRow.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name: providerName })).not.toBeVisible();
    });
  });

  test.describe('Tracing Provider', () => {
    const TRACING_CREATE_URL = `/a/${TEAM_SLUG}/service_providers/tracing/create/`;

    test('Add a tracing provider', async ({ page }) => {
      const providerName = `Tracing Provider ${Date.now()}`;

      // Navigate to team page and verify Tracing section exists
      await page.goto(TEAM_URL);
      await expect(page.getByRole('heading', { name: 'Tracing Providers' })).toBeVisible();

      // Navigate to tracing provider creation page
      await page.goto(TRACING_CREATE_URL);
      await expect(page.getByRole('heading', { name: 'Tracing Provider' })).toBeVisible();

      // Fill in required fields (Langfuse is default)
      await page.getByRole('textbox', { name: 'Name' }).fill(providerName);
      await expect(page.getByRole('combobox', { name: 'Type' })).toHaveValue('langfuse');
      await page.getByRole('textbox', { name: 'Secret Key' }).fill('sk-lf-mock-secret-key');
      await page.getByRole('textbox', { name: 'Public Key' }).fill('pk-lf-mock-public-key');
      await page.getByRole('textbox', { name: 'Host' }).fill('https://cloud.langfuse.com');

      // Click Create
      await page.getByRole('button', { name: 'Create' }).click();

      // Verify redirect to team page and provider appears
      await expect(page).toHaveURL(TEAM_URL);
      const providerRow = page.getByRole('row', { name: new RegExp(providerName) });
      await expect(providerRow.getByRole('cell', { name: providerName })).toBeVisible();
      await expect(providerRow.getByRole('cell', { name: 'Langfuse' })).toBeVisible();

      // Clean up: delete the provider
      await providerRow.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name: providerName })).not.toBeVisible();
    });
  });

  test('Delete multiple providers', async ({ page }) => {
    // Create one provider of each type, then delete them all

    // Create LLM Provider
    const llmName = `Del LLM ${Date.now()}`;
    await page.goto(`/a/${TEAM_SLUG}/service_providers/llm/create/`);
    await page.getByRole('textbox', { name: 'Name' }).fill(llmName);
    await page.getByRole('textbox', { name: /API Key/ }).fill('sk-test-mock-key-12345');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page).toHaveURL(TEAM_URL);
    await expect(page.getByRole('cell', { name: llmName })).toBeVisible();

    // Create Speech Provider
    const speechName = `Del Speech ${Date.now()}`;
    await page.goto(`/a/${TEAM_SLUG}/service_providers/voice/create/`);
    await page.getByRole('textbox', { name: 'Name' }).fill(speechName);
    await page.getByRole('textbox', { name: 'Access Key ID' }).fill('AKIAIOSFODNN7EXAMPLE');
    await page.getByRole('textbox', { name: 'Secret Access Key' }).fill('wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY');
    await page.getByRole('textbox', { name: 'Region' }).fill('us-east-1');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page).toHaveURL(TEAM_URL);
    await expect(page.getByRole('cell', { name: speechName })).toBeVisible();

    // Create Messaging Provider
    const messagingName = `Del Messaging ${Date.now()}`;
    await page.goto(`/a/${TEAM_SLUG}/service_providers/messaging/create/`);
    await page.getByRole('textbox', { name: 'Name' }).fill(messagingName);
    await page.getByRole('textbox', { name: 'Account SID' }).fill('mock-secret');
    await page.getByRole('textbox', { name: /Auth Token/ }).fill('mock-auth-token-12345');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page).toHaveURL(TEAM_URL);
    await expect(page.getByRole('cell', { name: messagingName })).toBeVisible();

    // Create Auth Provider
    const authName = `Del Auth ${Date.now()}`;
    await page.goto(`/a/${TEAM_SLUG}/service_providers/auth/create/`);
    await page.getByRole('textbox', { name: 'Name', exact: true }).fill(authName);
    await page.getByRole('textbox', { name: /Username/ }).fill('testuser');
    await page.getByRole('textbox', { name: 'Password' }).fill('testpass123');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page).toHaveURL(TEAM_URL);
    await expect(page.getByRole('cell', { name: authName })).toBeVisible();

    // Create Tracing Provider
    const tracingName = `Del Tracing ${Date.now()}`;
    await page.goto(`/a/${TEAM_SLUG}/service_providers/tracing/create/`);
    await page.getByRole('textbox', { name: 'Name' }).fill(tracingName);
    await page.getByRole('textbox', { name: 'Secret Key' }).fill('sk-lf-mock-secret-key');
    await page.getByRole('textbox', { name: 'Public Key' }).fill('pk-lf-mock-public-key');
    await page.getByRole('textbox', { name: 'Host' }).fill('https://cloud.langfuse.com');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page).toHaveURL(TEAM_URL);
    await expect(page.getByRole('cell', { name: tracingName })).toBeVisible();

    // Helper: delete a provider row and verify it's removed after page reload
    async function deleteProviderRow(name: string) {
      const row = page.getByRole('row', { name: new RegExp(name) });
      await row.getByRole('button').click();
      await confirmDeletion(page);
      await expect(page.getByRole('cell', { name })).not.toBeVisible({ timeout: 10000 });
    }

    // Now delete each provider and verify removal
    await deleteProviderRow(llmName);
    await deleteProviderRow(speechName);
    await deleteProviderRow(messagingName);
    await deleteProviderRow(authName);
    await deleteProviderRow(tracingName);
  });
});
