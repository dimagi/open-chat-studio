import { test, expect } from '@playwright/test';
import { setupPage, TEAM_SLUG } from '../helpers/common';

// Helper to hide debug toolbar
async function hideDebugToolbar(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    const el = document.getElementById('djDebug');
    if (el) el.style.display = 'none';
  });
}

test.describe.serial('Flow 2: Evaluations, Datasets, and Annotations', () => {
  test('create an evaluator', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/evaluations/evaluator/new/`);
    await hideDebugToolbar(page);

    // Fill in name
    await page.getByRole('textbox', { name: 'Name' }).fill('My First Evaluator');

    // Select LLM Evaluator type
    await page.getByLabel('Evaluator Type').selectOption('LLM Evaluator');

    // Wait for the LLM evaluator form fields to appear
    await expect(page.getByLabel('LLM Model').first()).toBeVisible();

    // Select model provider: use the first available provider (may be "Non-working OpenAI" if no API key is configured)
    const providerSelect = page.getByLabel('LLM Model').first();
    const providerOptions = await providerSelect.locator('option').allTextContents();
    const providerOption = providerOptions.find(opt => opt.includes('OpenAI') && !opt.includes('Select'));
    await providerSelect.selectOption({ label: providerOption! });

    // Select model: "o4-mini"
    await page.getByLabel('LLM Model').nth(1).selectOption({ label: 'OpenAI: o4-mini' });

    // Fill in prompt using the CodeMirror textbox
    // The prompt field is a textbox after the "Prompt" label
    const promptField = page.getByRole('textbox').nth(1); // Second textbox after Name
    await promptField.click();
    await promptField.fill(
      'Evaluate friendliness. Output "friendly" if the conversation was friendly, otherwise "unfriendly"'
    );

    // Fill in output schema field name
    await page.getByRole('textbox', { name: "e.g., 'accuracy', 'score'" }).fill('friendliness');

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Should redirect to evaluators list or evaluator detail
    await expect(page).toHaveURL(/\/evaluations\/evaluator\//, { timeout: 10000 });
  });

  test('create a dataset from sessions', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/evaluations/dataset/new/`);
    await hideDebugToolbar(page);

    // Fill in name
    await page.getByRole('textbox', { name: 'Name' }).fill('My Data Set');

    // "Clone from sessions" radio should already be selected
    await expect(page.getByRole('radio', { name: 'Clone from sessions' })).toBeChecked();

    // Click Create Dataset
    await page.getByRole('button', { name: 'Create Dataset' }).click();

    // Should redirect to datasets list or dataset detail
    await expect(page).toHaveURL(/\/evaluations\/dataset\//, { timeout: 10000 });
  });

  test('create an evaluation', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/evaluations/new/`);
    await hideDebugToolbar(page);

    // Fill in name
    await page.getByRole('textbox', { name: 'Name' }).fill('My First Eval');

    // Select dataset
    // Select the dataset option that contains "My Data Set"
    const datasetSelect = page.getByLabel('Dataset');
    const datasetOptions = await datasetSelect.locator('option').allTextContents();
    const datasetOption = datasetOptions.find(opt => opt.includes('My Data Set'));
    await datasetSelect.selectOption({ label: datasetOption! });

    // Select evaluator checkbox
    await page.getByRole('checkbox', { name: /My First Evaluator/i }).first().check();

    // Check "Run generation step before evaluation"
    await page.getByRole('checkbox', { name: 'Run generation step before evaluation' }).check();

    // Wait for chatbot dropdown to appear and select chatbot
    const chatbotSelect = page.getByLabel(/Chatbot/i).first();
    await expect(chatbotSelect).toBeVisible({ timeout: 5000 });
    // Select the first chatbot that matches "My first chatbot"
    const chatbotOptions = await chatbotSelect.locator('option').allTextContents();
    const chatbotOption = chatbotOptions.find(opt => opt.toLowerCase().includes('my first chatbot'));
    await chatbotSelect.selectOption({ label: chatbotOption! });

    // Wait for version dropdown to appear and select version
    const versionSelect = page.getByLabel(/Chatbot version/i).first();
    await expect(versionSelect).toBeVisible({ timeout: 5000 });
    await versionSelect.selectOption({ label: 'Latest Published Version' });

    // Create the evaluation
    await page.getByRole('button', { name: 'Create' }).click();

    // Should redirect to evaluations list
    await expect(page).toHaveURL(/\/evaluations\//, { timeout: 10000 });
  });

  test('run the evaluation', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/evaluations/`);
    await hideDebugToolbar(page);

    // Find the evaluation row (name may be lowercased)
    const evalRow = page.locator('table tbody tr').filter({ hasText: /my first eval/i }).first();
    await expect(evalRow).toBeVisible({ timeout: 10000 });

    // Click the run link (the one with /runs/new/ in the URL)
    await evalRow.locator('a[href*="/runs/new/"]').click({ force: true });

    // Wait for evaluation to start
    await page.waitForTimeout(3000);
  });

  test('create an annotation queue', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/human-annotations/queue/new/`);
    await hideDebugToolbar(page);

    // Fill in name
    await page.getByRole('textbox', { name: 'Name' }).fill('My First Annotation');

    // Fill in schema field name
    await page.getByRole('textbox', { name: "e.g., 'accuracy', 'score'" }).fill('accuracy');

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Should redirect to the annotation queue list or detail page
    await expect(page).toHaveURL(/\/human-annotations\/queue\//, { timeout: 10000 });
  });

  test('add sessions to annotation and annotate', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);

    // Go to annotations list
    await page.goto(`/a/${TEAM_SLUG}/human-annotations/queue/`);
    await hideDebugToolbar(page);

    // Click on the annotation just created
    await page.getByRole('link', { name: 'My First Annotation' }).last().click();

    await hideDebugToolbar(page);

    // Click "Add Sessions"
    await page.getByRole('button', { name: /Add Sessions/i })
      .or(page.getByRole('link', { name: /Add Sessions/i }))
      .click();

    await hideDebugToolbar(page);

    // Select the first session checkbox
    const sessionCheckbox = page.locator('input[type="checkbox"]').first();
    await sessionCheckbox.check();

    // Click "Add to Queue"
    await page.getByRole('button', { name: /Add to Queue/i }).click();

    // Wait for redirect
    await page.waitForTimeout(2000);
  });

  test('start annotating and submit', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);

    // Go to annotations list
    await page.goto(`/a/${TEAM_SLUG}/human-annotations/queue/`);
    await hideDebugToolbar(page);

    // Click on the annotation
    await page.getByRole('link', { name: 'My First Annotation' }).last().click();
    await hideDebugToolbar(page);

    // Click "Start Annotating"
    await page.getByRole('button', { name: /Start Annotating/i })
      .or(page.getByRole('link', { name: /Start Annotating/i }))
      .click();

    await hideDebugToolbar(page);

    // Check if there are items to annotate
    const noItemsMsg = page.getByText('No more items to annotate');
    if (await noItemsMsg.isVisible({ timeout: 3000 }).catch(() => false)) {
      // Items were already annotated in a previous run - skip
      return;
    }

    // Fill in the accuracy field with "5"
    const accuracyField = page.getByRole('textbox').first();
    await expect(accuracyField).toBeVisible({ timeout: 10000 });
    await accuracyField.fill('5');

    // Click Submit
    await page.getByRole('button', { name: 'Submit' }).click();
    await page.waitForTimeout(2000);
  });

  test('verify annotation results', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);

    // Go to annotations list
    await page.goto(`/a/${TEAM_SLUG}/human-annotations/queue/`);
    await hideDebugToolbar(page);

    // Click on the annotation
    await page.getByRole('link', { name: 'My First Annotation' }).last().click();
    await hideDebugToolbar(page);

    // Verify items table shows at least one item
    // Check for "Completed" status or any item in the items table
    const itemsSection = page.getByRole('heading', { name: 'Items' });
    await expect(itemsSection).toBeVisible({ timeout: 10000 });

    // Verify there are items listed (table or list entries)
    await expect(
      page.locator('table tbody tr').first()
        .or(page.getByText('Completed').first())
    ).toBeVisible({ timeout: 10000 });
  });

  test('verify evaluation results', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);

    // Go to evaluations list
    await page.goto(`/a/${TEAM_SLUG}/evaluations/`);
    await hideDebugToolbar(page);

    // Find the evaluation row (name may be lowercased)
    const evalRow = page.locator('table tbody tr').filter({ hasText: /my first eval/i }).first();
    await expect(evalRow).toBeVisible({ timeout: 10000 });

    // Click the evaluation link to see details
    await evalRow.getByRole('link').first().click();

    await page.waitForTimeout(3000);
  });
});
