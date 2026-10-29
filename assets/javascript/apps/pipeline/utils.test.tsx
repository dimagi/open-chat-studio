import {beforeAll, describe, expect, it} from 'vitest';
import {getCachedData, renderRouterPromptPreview} from './utils';
import {readFileSync} from 'node:fs';

// Shared with the router's own tests in apps/pipelines/tests/test_router_prompt.py, so the preview
// fails CI when it stops matching the prompt the router sends. The path is from the repo root, where vitest runs.
const previewCases = JSON.parse(
  readFileSync('apps/pipelines/tests/data/router_prompt_preview_cases.json', 'utf8'),
) as {
  template: string;
  cases: Array<{
    id: string;
    keywords: string[];
    route_descriptions: Record<string, string>;
    default_keyword_index: number;
    prompt_mode: string;
    custom_context: string;
    expected: string;
  }>;
};

beforeAll(() => {
  const setScript = (id: string, data: unknown) => {
    const el = document.createElement('script');
    el.type = 'application/json';
    el.id = id;
    el.textContent = JSON.stringify(data);
    document.body.appendChild(el);
  };
  setScript('parameter-values', {});
  setScript('default-values', {});
  setScript('node-schemas', [{title: 'RouterNode', 'ui:label': 'Router', properties: {}}]);
  setScript('flags-enabled', []);
  setScript('llm-model-params', {});
  setScript('llm-model-parameter-schemas', {});
});

describe('getCachedData', () => {
  it('parses the DOM script tags once and returns the same nodeSchemas reference on every call', () => {
    // getCachedData() always returns the same mutated singleton object, so the values
    // themselves must be captured at each call, not just the wrapper it returns.
    const firstSchemas = getCachedData().nodeSchemas;
    const firstValues = getCachedData().parameterValues;
    const secondSchemas = getCachedData().nodeSchemas;
    const secondValues = getCachedData().parameterValues;
    expect(secondSchemas).toBe(firstSchemas);
    expect(secondValues).toBe(firstValues);
  });
});

describe('renderRouterPromptPreview', () => {
  const template = 'Routes:\n{routes}\n{custom_context}\nDefault: {default_route}';
  const routes = {
    keywords: ['BILLING', 'SUPPORT', ''],
    routeDescriptions: {BILLING: 'Payments and invoices', SUPPORT: '  '},
    defaultRoute: 'BILLING',
  };

  it('lists each route with its description and fills the default route', () => {
    expect(renderRouterPromptPreview(template, {...routes, customContext: ''})).toBe(
      'Routes:\n- BILLING: Payments and invoices\n- SUPPORT\n\nDefault: BILLING'
    );
  });

  it('adds the context, filling the route variables and leaving the others as written', () => {
    const preview = renderRouterPromptPreview(template, {
      ...routes,
      customContext: ' Prefer {routes_list} for {participant_data.plan}. ',
    });

    expect(preview).toContain('\nAdditional context:\nPrefer BILLING, SUPPORT for {participant_data.plan}.\n');
  });

  it('shows escaped braces in the context as the literal braces the router sends', () => {
    const preview = renderRouterPromptPreview(template, {...routes, customContext: 'Write {{routes_list}} as is.'});

    expect(preview).toContain('\nWrite {routes_list} as is.\n');
  });

  it('inserts descriptions and context as written, without treating $ as a replacement pattern', () => {
    const preview = renderRouterPromptPreview(template, {
      ...routes,
      routeDescriptions: {BILLING: 'Costs $& fees'},
      customContext: "Refunds over $' go to billing.",
    });

    expect(preview).toContain('- BILLING: Costs $& fees');
    expect(preview).toContain("Refunds over $' go to billing.");
  });
});

describe('renderRouterPromptPreview matches the router', () => {
  it.each(previewCases.cases.map((testCase) => [testCase.id, testCase] as const))('%s', (_id, testCase) => {
    const preview = renderRouterPromptPreview(previewCases.template, {
      keywords: testCase.keywords,
      routeDescriptions: testCase.route_descriptions,
      customContext: testCase.prompt_mode === 'standard_with_context' ? testCase.custom_context : '',
      defaultRoute: testCase.keywords[testCase.default_keyword_index],
    });

    expect(preview).toBe(testCase.expected);
  });
});
