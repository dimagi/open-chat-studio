import {beforeAll, describe, expect, it} from 'vitest';
import {getCachedData} from './utils';

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
