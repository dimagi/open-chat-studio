import {afterEach, describe, expect, it} from 'vitest';
import {render} from '@testing-library/react';
import {DeprecationNotice} from './PipelineNode';
import type {JsonSchema} from './types/nodeParams';
import usePipelineStore from './stores/pipelineStore';

const schema = (overrides: Partial<JsonSchema> = {}) => ({properties: {}, ...overrides}) as JsonSchema;

afterEach(() => {
  usePipelineStore.setState({deprecatedModels: {}});
});

describe('DeprecationNotice', () => {
  it('still says where to go when the deprecated model names no replacement', () => {
    // Most deprecated models declare no replacement, so the branch that reads one is the
    // exceptional path. Collapsing the two into `{replacement && ...}` leaves those nodes with
    // a warning badge and no advice, which renders and reads fine — nothing else would fail.
    usePipelineStore.setState({deprecatedModels: {'node-1': {model: 'gpt-5', replacement: null}}});

    const {container} = render(<DeprecationNotice nodeId="node-1" nodeSchema={schema()} />);

    expect(container.textContent).toContain('gpt-5');
    expect(container.textContent).toContain('moved to a supported model');
  });

  it('reports a deprecated type and a deprecated model under one badge', () => {
    // The two warnings share a badge because a node can carry both at once. A third source
    // added later is most naturally written as its own badge, which silently gives such a node
    // two triangles to tell apart rather than failing.
    usePipelineStore.setState({deprecatedModels: {'node-1': {model: 'gpt-5', replacement: 'gpt-5.4'}}});

    const {getAllByRole, container} = render(
      <DeprecationNotice nodeId="node-1" nodeSchema={schema({'ui:deprecated': true})} />,
    );

    expect(getAllByRole('button')).toHaveLength(1);
    expect(container.textContent).toContain('This node type has been deprecated');
    expect(container.textContent).toContain('gpt-5.4');
  });
});
