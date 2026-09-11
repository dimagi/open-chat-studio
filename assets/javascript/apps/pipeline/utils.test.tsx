import {beforeAll, describe, expect, it} from 'vitest';
import {buildTypeChangeParams, getCachedData, getDefaultParamValues} from './utils';
import type {JsonSchema} from './types/nodeParams';

beforeAll(() => {
  const setScript = (id: string, data: unknown) => {
    const el = document.createElement('script');
    el.type = 'application/json';
    el.id = id;
    el.textContent = JSON.stringify(data);
    document.body.appendChild(el);
  };
  setScript('parameter-values', {});
  setScript('default-values', {greeting: 'hi'});
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

const llmSchema: JsonSchema = {
  title: 'LLMResponseWithPrompt',
  'ui:flow_node_type': 'pipelineNode',
  'ui:label': 'LLM',
  'ui:can_add': true,
  'ui:can_delete': true,
  'ui:deprecated': false,
  properties: {
    prompt: {type: 'string', default: ''},
    greeting: {type: 'string'},
  },
};

const templateSchema: JsonSchema = {
  title: 'RenderTemplate',
  'ui:flow_node_type': 'pipelineNode',
  'ui:label': 'Render a template',
  'ui:can_add': true,
  'ui:can_delete': true,
  'ui:deprecated': false,
  properties: {
    template_string: {type: 'string', default: 'hello'},
  },
};

describe('getDefaultParamValues', () => {
  it('always includes an empty name, whatever the schema declares', () => {
    expect(getDefaultParamValues(templateSchema).name).toBe('');
  });

  it('keeps name empty even when the schema itself declares a name property', () => {
    // Every real node schema declares "name" (apps/pipelines/nodes/base.py's BasePipelineNode
    // field), so the defaults loop iterates over it like any other property -- it must not let
    // that overwrite the empty starting value with a schema/cached default or null.
    const schema: JsonSchema = {
      ...templateSchema,
      properties: {...templateSchema.properties, name: {type: 'string', default: 'Untitled'}},
    };
    expect(getDefaultParamValues(schema).name).toBe('');
  });

  it("uses a property's own default when it has one", () => {
    expect(getDefaultParamValues(templateSchema).template_string).toBe('hello');
  });

  it('falls back to the cached default-values map when the property has no default of its own', () => {
    expect(getDefaultParamValues(llmSchema).greeting).toBe('hi');
  });

  it('is null when neither the schema nor the default-values map has a value', () => {
    const schema: JsonSchema = {...templateSchema, properties: {undeclared: {type: 'string'}}};
    expect(getDefaultParamValues(schema).undeclared).toBeNull();
  });
});

describe('buildTypeChangeParams (#1452)', () => {
  it("resets to the new type's own defaults", () => {
    const params = buildTypeChangeParams(templateSchema, {name: 'my-llm', prompt: 'Be terse.'});
    expect(params.template_string).toBe('hello');
  });

  it('drops params the new type does not declare', () => {
    const params = buildTypeChangeParams(templateSchema, {name: 'my-llm', prompt: 'Be terse.'});
    expect(params).not.toHaveProperty('prompt');
  });

  it('preserves the node name across the swap', () => {
    const params = buildTypeChangeParams(templateSchema, {name: 'my-llm', prompt: 'Be terse.'});
    expect(params.name).toBe('my-llm');
  });

  it('preserves color when the node has one, since color is UI state, not a schema field on any type', () => {
    const params = buildTypeChangeParams(templateSchema, {name: 'n', color: 'bg-red-100 dark:bg-red-950'});
    expect(params.color).toBe('bg-red-100 dark:bg-red-950');
  });

  it('adds no color key when the node never had one', () => {
    const params = buildTypeChangeParams(templateSchema, {name: 'n'});
    expect(params).not.toHaveProperty('color');
  });
});
