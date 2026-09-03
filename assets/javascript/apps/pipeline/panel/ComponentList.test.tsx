import {beforeAll, describe, expect, it} from 'vitest';
import {render} from '@testing-library/react';
import ComponentList from './ComponentList';

// getCachedData() (assets/javascript/apps/pipeline/utils.tsx) reads these script tags once
// and caches the result for the lifetime of the module.
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

describe('ComponentList', () => {
  it('mounts and re-renders on isOpen changes without an infinite update loop', () => {
    // getHelpOffState/hideHelp used to be recreated every render and passed straight into
    // useEffect's dep array — naively "fixing" that lint warning by adding the unmemoized
    // function to the deps would re-fire the effect (and its setState) every render. This
    // just needs to mount and re-render cleanly; a regression here throws "Maximum update
    // depth exceeded" or hangs, it doesn't quietly fail an assertion.
    const {rerender} = render(<ComponentList isOpen={false} setIsOpen={() => {}} />);
    expect(() => rerender(<ComponentList isOpen={true} setIsOpen={() => {}} />)).not.toThrow();
  });
});
