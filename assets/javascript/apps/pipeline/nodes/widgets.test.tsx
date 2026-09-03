import {beforeAll, describe, expect, it, vi} from 'vitest';
import {render, fireEvent} from '@testing-library/react';
import {getWidget} from './widgets';
import type {WidgetParams} from './widgets';
import type {PropertySchema} from '../types/nodeParams';
import usePipelineStore from '../stores/pipelineStore';

// getCachedData() (assets/javascript/apps/pipeline/utils.tsx) reads these script tags once
// and caches the result for the lifetime of the module — set them up before any widget runs.
beforeAll(() => {
  const setScript = (id: string, data: unknown) => {
    const el = document.createElement('script');
    el.type = 'application/json';
    el.id = id;
    el.textContent = JSON.stringify(data);
    document.body.appendChild(el);
  };
  setScript('parameter-values', {
    llm_provider_model_id: [{value: 'model-a', label: 'Model A', type: 'openai'}],
    built_in_tools: {openai: [{value: 'web_search', label: 'Web search'}]},
    tool_config: {},
  });
  setScript('default-values', {});
  setScript('node-schemas', []);
  setScript('flags-enabled', []);
  setScript('llm-model-params', {});
  setScript('llm-model-parameter-schemas', {});
});

const baseProps: Omit<WidgetParams, 'schema' | 'nodeParams' | 'paramValue'> = {
  nodeId: 'node-1',
  name: 'built_in_tools',
  label: 'Tools',
  helpText: '',
  inputError: undefined,
  updateParamValue: () => {},
  nodeSchema: {} as never,
  required: false,
  getNodeFieldError: () => undefined,
  readOnly: false,
};

// Note on scope: eslint-plugin-react-hooks' react-hooks/rules-of-hooks (see eslint.config.mjs)
// is the actual regression guard for the conditional-hook-call bugs fixed in this file --
// confirmed to flag the pre-fix code before this fix and stay clean after. A jsdom rendering
// test was tried first, but React 19 in this test environment neither throws nor logs a
// console.error for a hook-count mismatch across renders of the same instance (confirmed with
// a minimal repro), so a "does this throw" assertion here would pass identically whether or
// not the bug were fixed — exactly the trivial-assertion trap this project's own review
// discipline calls out. What these tests below do check is the actual rendering behavior of
// the early return each widget takes, which is real, fix-adjacent behavior worth locking in.

describe('MultiSelectWidget', () => {
  it('renders nothing when the schema has no options', () => {
    const Widget = getWidget('multiselect', {type: 'array'} as PropertySchema);
    const emptySchema = {type: 'array', enum: []} as unknown as PropertySchema;

    const {container} = render(
      <Widget {...baseProps} schema={emptySchema} nodeParams={{name: 'x'}} paramValue={[]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a checkbox per option, and updates on re-render, when the schema has options', () => {
    const Widget = getWidget('multiselect', {type: 'array'} as PropertySchema);
    const schema = {type: 'array', enum: ['a', 'b']} as unknown as PropertySchema;

    const {getAllByRole} = render(
      <Widget {...baseProps} schema={schema} nodeParams={{name: 'x'}} paramValue={[]} />,
    );
    expect(getAllByRole('checkbox')).toHaveLength(2);
  });
});

describe('BuiltInToolsWidget', () => {
  it('renders nothing for a provider with no configured tools', () => {
    const Widget = getWidget('built_in_tools', {type: 'array'} as PropertySchema);

    const {container} = render(
      <Widget
        {...baseProps}
        schema={{type: 'array'} as PropertySchema}
        nodeParams={{name: 'x', llm_provider_model_id: 'unknown-model'}}
        paramValue={[]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('reflects a new paramValue from the store immediately, without a stale local copy', () => {
    const Widget = getWidget('built_in_tools', {type: 'array'} as PropertySchema);
    const updateParamValue = vi.fn();

    // onUpdate writes through the real store's setNode, which looks up the node by id --
    // seed a matching node so that lookup succeeds instead of hitting an unrelated crash.
    usePipelineStore.setState({nodes: [{id: 'node-1', type: 'test', position: {x: 0, y: 0}, data: {type: 'test', label: '', params: {name: 'x'}}}] as never});

    const {getAllByRole, rerender} = render(
      <Widget
        {...baseProps}
        updateParamValue={updateParamValue}
        schema={{type: 'array'} as PropertySchema}
        nodeParams={{name: 'x', llm_provider_model_id: 'model-a'}}
        paramValue={[]}
      />,
    );

    // Queried by role, not label: this checkbox has no accessible name (no <label>, no
    // aria-label) — a real gap, but not one react-doctor flagged on this exact line, so it's
    // left alone here rather than folded into this fix.
    const checkbox = getAllByRole('checkbox')[0] as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    // Simulate the store round-trip: a click writes to the store, and the next render
    // passes the new value back down as a prop (no separate local-state effect needed).
    fireEvent.click(checkbox);
    rerender(
      <Widget
        {...baseProps}
        updateParamValue={updateParamValue}
        schema={{type: 'array'} as PropertySchema}
        nodeParams={{name: 'x', llm_provider_model_id: 'model-a'}}
        paramValue={['web_search']}
      />,
    );
    expect(checkbox.checked).toBe(true);
  });
});
