import {beforeAll, describe, expect, it} from 'vitest';
import {getCachedData, renderRouterPromptPreview} from './utils';

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
});
