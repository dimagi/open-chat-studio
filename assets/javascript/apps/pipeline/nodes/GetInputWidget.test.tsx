import {describe, expect, it, vi} from 'vitest';
import {render} from '@testing-library/react';
import {getNodeInputWidget, getWidgets, VisibleWhenWrapper} from './GetInputWidget';
import type {JsonSchema, InputWidgetParams} from '../types/nodeParams';

const noop = () => {};

const baseSchema = {
  title: 'Test node',
  'ui:flow_node_type': 'test',
  'ui:label': 'Test node',
  'ui:can_add': true,
  'ui:can_delete': true,
  'ui:deprecated': false,
  properties: {
    name: {type: 'string'},
    greeting: {type: 'string'},
  },
} as unknown as JsonSchema;

describe('getNodeInputWidget', () => {
  it('runs without a React hook call, given getNodeFieldError/readOnly as arguments', () => {
    // getNodeType has no entry in the node-type allow-list, so the "greeting" param
    // passes both early filters and reaches the code path that used to call
    // usePipelineStore() directly inside this plain (non-component) function.
    const param = {
      id: 'node-1',
      name: 'greeting',
      schema: baseSchema,
      params: {name: 'x', greeting: 'hi'},
      updateParamValue: noop,
      nodeType: 'UnlistedNodeType',
      required: false,
    } as unknown as InputWidgetParams;

    expect(() => getNodeInputWidget(param, () => undefined, false)).not.toThrow();
  });
});

describe('getWidgets', () => {
  it('runs without a React hook call, given getNodeFieldError/readOnly as arguments', () => {
    expect(() =>
      getWidgets(
        {
          schema: baseSchema,
          nodeId: 'node-1',
          nodeData: {type: 'UnlistedNodeType', label: 'Test', params: {name: 'x', greeting: 'hi'}},
          updateParamValue: noop,
        },
        {getNodeFieldError: () => undefined, readOnly: false},
      ),
    ).not.toThrow();
  });
});

describe('VisibleWhenWrapper', () => {
  it('reapplies the hidden-on-mount clear when the field identity changes, even if visibility does not', () => {
    const onHideA = vi.fn();
    const onHideB = vi.fn();

    const {rerender} = render(
      <VisibleWhenWrapper
        visibleWhen={{field: 'toggle', value: true}}
        nodeParams={{name: 'x', toggle: false}}
        fieldName="fieldA"
        nodeId="node-1"
        schemaDefault={null}
        onHide={onHideA}
      >
        <span>content</span>
      </VisibleWhenWrapper>,
    );
    expect(onHideA).toHaveBeenCalledTimes(1);

    // Same node, a different field, still hidden. This simulates the widget list
    // re-using the same VisibleWhenWrapper instance (React doesn't remount it just
    // because a prop changed) for a different schema field.
    rerender(
      <VisibleWhenWrapper
        visibleWhen={{field: 'toggle', value: true}}
        nodeParams={{name: 'x', toggle: false}}
        fieldName="fieldB"
        nodeId="node-1"
        schemaDefault={null}
        onHide={onHideB}
      >
        <span>content</span>
      </VisibleWhenWrapper>,
    );

    expect(onHideB).toHaveBeenCalledTimes(1);
  });
});
