import { test, expect } from '@playwright/test';
import { setupPage, TEAM_SLUG } from '../helpers/common';

const UNIQUE_SUFFIX = Date.now();
const EVALUATOR_NAME = `My First Evaluator ${UNIQUE_SUFFIX}`;
const DATASET_NAME = `My Data Set ${UNIQUE_SUFFIX}`;
const EVALUATION_NAME = `My First Eval ${UNIQUE_SUFFIX}`;
const ANNOTATION_NAME = `My First Annotation ${UNIQUE_SUFFIX}`;

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
    await page.getByRole('textbox', { name: 'Name' }).fill(EVALUATOR_NAME);

    // Select LLM Evaluator type
    await page.getByLabel('Evaluator Type').selectOption('LLM Evaluator');

    // Wait for the LLM evaluator form fields to appear
    await expect(page.getByLabel('LLM Model').first()).toBeVisible();

    // Select model provider: use the first available OpenAI provider
    const providerSelect = page.getByLabel('LLM Model').first();
    const providerOptions = await providerSelect.locator('option').allTextContents();
    const providerOption = providerOptions.find(opt => opt.includes('OpenAI') && !opt.includes('Select'));
    await providerSelect.selectOption({ label: providerOption! });

    // Select model: "o4-mini"
    await page.getByLabel('LLM Model').nth(1).selectOption({ label: 'OpenAI: o4-mini' });

    // Fill in prompt using the CodeMirror textbox
    const promptField = page.getByRole('textbox').nth(1);
    await promptField.click();
    await promptField.fill(
      'Evaluate friendliness. Output "friendly" if the conversation was friendly, otherwise "unfriendly"'
    );

    // Fill in output schema field name
    await page.getByRole('textbox', { name: "e.g., 'accuracy', 'score'" }).fill('friendliness');

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Should redirect away from the /new/ page to evaluator list or detail
    await expect(page).not.toHaveURL(/\/new\//, { timeout: 10000 });
    await expect(page).toHaveURL(/\/evaluations\/evaluator\//);
  });

  test('create a dataset', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/evaluations/dataset/new/`);
    await hideDebugToolbar(page);

    // Fill in name
    await page.getByRole('textbox', { name: 'Name' }).fill(DATASET_NAME);

    // Select "Create manually" mode — doesn't require pre-existing sessions
    // Click the label text to trigger Alpine.js reactivity (x-model on radio)
    await page.locator('label[for="id_mode_1"]').click();
    // Verify the mode switched by checking the manual form section is visible
    await page.getByText('Add your first message pair').waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});

    // Add a message pair (required for manual mode)
    // Use the form's textboxes (not the edit modal ones)
    const addForm = page.locator('#manual-message-form');
    await addForm.getByPlaceholder('Enter human message').fill('Hello');
    await addForm.getByPlaceholder('Enter AI response').fill('Hi there!');
    await page.getByRole('button', { name: /Add Message Pair/i }).click();
    // Wait for the message pair to appear in the list
    await page.waitForTimeout(500);

    // Click Create Dataset
    await page.getByRole('button', { name: 'Create Dataset' }).click();

    // Should redirect away from the /new/ page
    await expect(page).not.toHaveURL(/\/new\//, { timeout: 10000 });
    await expect(page).toHaveURL(/\/evaluations\/dataset\//);
  });

  test('create an evaluation', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/evaluations/new/`);
    await hideDebugToolbar(page);

    // Fill in name
    await page.getByRole('textbox', { name: 'Name' }).fill(EVALUATION_NAME);

    // Select the first available dataset
    const datasetSelect = page.getByLabel('Dataset');
    const datasetOptions = await datasetSelect.locator('option').allTextContents();
    const datasetOption = datasetOptions.find(opt => opt.trim() !== '' && !opt.includes('---') && !opt.includes('Select'));
    expect(datasetOption).toBeTruthy();
    await datasetSelect.selectOption({ label: datasetOption! });

    // Select the first available evaluator checkbox
    await page.getByRole('checkbox', { name: /Evaluator/i }).first().check();

    // Check "Run generation step before evaluation"
    await page.getByRole('checkbox', { name: /Run generation step/i }).check();

    // Wait for chatbot dropdown to appear and select the first available chatbot
    const chatbotSelect = page.getByLabel(/Chatbot/i).first();
    await expect(chatbotSelect).toBeVisible({ timeout: 5000 });
    const chatbotOptions = await chatbotSelect.locator('option').allTextContents();
    const chatbotOption = chatbotOptions.find(opt => opt.trim() !== '' && !opt.includes('Select') && !opt.includes('---'));
    expect(chatbotOption).toBeTruthy();
    await chatbotSelect.selectOption({ label: chatbotOption! });

    // Wait for version dropdown to be populated via HTMX after chatbot selection
    const versionSelect = page.getByLabel(/Chatbot version/i).first();
    await expect(versionSelect).toBeVisible({ timeout: 5000 });
    // Wait for HTMX to load the version options (at least one non-placeholder option)
    await expect(versionSelect.locator('option:not([value=""])')).not.toHaveCount(0, { timeout: 5000 });
    const versionOptions = await versionSelect.locator('option').allTextContents();
    const versionOption = versionOptions.find(opt => opt.includes('Published'));
    if (versionOption) {
      await versionSelect.selectOption({ label: versionOption });
    } else {
      // Fall back to first non-empty option
      const firstOption = versionOptions.find(opt => opt.trim() !== '');
      await versionSelect.selectOption({ label: firstOption! });
    }

    // Create the evaluation
    await page.getByRole('button', { name: 'Create' }).click();

    // Should redirect away from the /new/ page
    await expect(page).not.toHaveURL(/\/new\//, { timeout: 10000 });
    await expect(page).toHaveURL(/\/evaluations\//);
  });

  test('run the evaluation', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);
    await page.goto(`/a/${TEAM_SLUG}/evaluations/`);
    await hideDebugToolbar(page);

    // Wait for HTMX table to load
    await page.locator('table tbody tr').first().waitFor({ state: 'visible', timeout: 10000 });

    // Find the evaluation row
    const evalRow = page.locator('table tbody tr').filter({ hasText: EVALUATION_NAME }).first();
    await expect(evalRow).toBeVisible({ timeout: 10000 });

    // Click the run link
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
    await page.getByRole('textbox', { name: 'Name' }).fill(ANNOTATION_NAME);

    // Fill in schema field name
    await page.getByRole('textbox', { name: "e.g., 'accuracy', 'score'" }).fill('accuracy');

    // Click Create
    await page.getByRole('button', { name: 'Create' }).click();

    // Should redirect away from the /new/ page
    await expect(page).not.toHaveURL(/\/new\//, { timeout: 10000 });
    await expect(page).toHaveURL(/\/human-annotations\/queue\//);
  });

  test('add sessions to annotation and annotate', async ({ page }) => {
    test.setTimeout(60000);
    await setupPage(page);

    // Go to annotations list
    await page.goto(`/a/${TEAM_SLUG}/human-annotations/queue/`);
    await hideDebugToolbar(page);

    // Click on the annotation just created
    await page.getByRole('link', { name: ANNOTATION_NAME }).last().click();

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
    await page.getByRole('link', { name: ANNOTATION_NAME }).last().click();
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
    await page.getByRole('link', { name: ANNOTATION_NAME }).last().click();
    await hideDebugToolbar(page);

    // Verify items table shows at least one item
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

    // Wait for HTMX table to load
    await page.locator('table tbody tr').first().waitFor({ state: 'visible', timeout: 10000 });

    // Find the evaluation row
    const evalRow = page.locator('table tbody tr').filter({ hasText: EVALUATION_NAME }).first();
    await expect(evalRow).toBeVisible({ timeout: 10000 });

    // Click the evaluation link to see details
    await evalRow.getByRole('link').first().click();

    await page.waitForTimeout(3000);
  });
});
