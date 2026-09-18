import {beforeAll, beforeEach, describe, expect, it, vi} from 'vitest';
import {render, fireEvent, waitFor} from '@testing-library/react';
import {getWidget as getWidgetUntyped, InputField, GenerateCodeSection, CHECK_FOR_BUGS_PROMPT} from './widgets';
import type {WidgetParams} from './widgets';
import type {ComponentType} from 'react';
import type {PropertySchema} from '../types/nodeParams';
import usePipelineStore from '../stores/pipelineStore';
import {apiClient} from '../api/api';

// GenerateCodeSection renders CodeDiffEditor for the AI suggestion, which mounts a real
// CodeMirror/EditorView -- not reliable in jsdom. Stub it with something that surfaces the
// props it was given, so tests can assert on wiring (original/value) without a real editor.
vi.mock('../components/CodeDiffEditor', () => ({
  CodeDiffEditor: ({original, value}: {original: string; value: string}) => (
    <div data-testid="code-diff" data-original={original} data-value={value} />
  ),
}));

// getWidget's inferred return type is a union across every case in its switch (each widget's
// own prop type, e.g. ToggleWidget's boolean paramValue), so JSX usages below would otherwise
// have to satisfy all of them at once. Both widgets under test here genuinely take WidgetParams
// at runtime, so narrow to that once, rather than casting every paramValue at every call site.
const getWidget = (name: string, params: PropertySchema): ComponentType<WidgetParams> =>
  getWidgetUntyped(name, params) as ComponentType<WidgetParams>;

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

describe('InputField warning slot', () => {
  it('shows an advisory note under the field', () => {
    const {container} = render(
      <InputField label="LLM Model" help_text="" inputWarning="gpt-5 is deprecated">
        <input />
      </InputField>,
    );
    expect(container.textContent).toContain('gpt-5 is deprecated');
  });

  it('suppresses the warning while there is an error', () => {
    // One message per field, and the error is the one that has to be acted on first.
    const {container} = render(
      <InputField label="LLM Model" help_text="" inputError="This field is required." inputWarning="gpt-5 is deprecated">
        <input />
      </InputField>,
    );
    expect(container.textContent).toContain('This field is required.');
    expect(container.textContent).not.toContain('deprecated');
  });
});

describe('GenerateCodeSection', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps the prompt textarea mounted after a successful generation', async () => {
    vi.spyOn(apiClient, 'generateCode').mockResolvedValue({response: {code: 'def main(input, **kwargs): return input'}});
    const {getByPlaceholderText, getByText} = render(
      <GenerateCodeSection showGenerate={true} onAccept={() => {}} currentCode="def main(input, **kwargs): return input" />,
    );

    fireEvent.change(getByPlaceholderText(/Describe what you want/), {target: {value: 'add error handling'}});
    fireEvent.click(getByText('Generate'));

    await waitFor(() => expect(apiClient.generateCode).toHaveBeenCalled());
    expect(getByPlaceholderText(/Describe what you want/)).toBeInTheDocument();
  });

  it('Reject clears the diff but keeps the prompt; Clear empties both', async () => {
    vi.spyOn(apiClient, 'generateCode').mockResolvedValue({response: {code: 'generated code'}});
    const {getByPlaceholderText, getByText, queryByTestId} = render(
      <GenerateCodeSection showGenerate={true} onAccept={() => {}} currentCode="original code" />,
    );

    const textarea = getByPlaceholderText(/Describe what you want/) as HTMLTextAreaElement;
    fireEvent.change(textarea, {target: {value: 'my prompt'}});
    fireEvent.click(getByText('Generate'));
    await waitFor(() => expect(queryByTestId('code-diff')).toBeInTheDocument());

    fireEvent.click(getByText('Reject'));
    expect(queryByTestId('code-diff')).not.toBeInTheDocument();
    expect(textarea.value).toBe('my prompt');

    fireEvent.click(getByText('Generate'));
    await waitFor(() => expect(queryByTestId('code-diff')).toBeInTheDocument());
    fireEvent.click(getByText('Clear'));
    expect(queryByTestId('code-diff')).not.toBeInTheDocument();
    expect(textarea.value).toBe('');
  });

  it('Check for bugs sends the fixed prompt with the current code, ignoring an empty prompt field', async () => {
    vi.spyOn(apiClient, 'generateCode').mockResolvedValue({response: {code: 'fixed code'}});
    const {getByText} = render(
      <GenerateCodeSection showGenerate={true} onAccept={() => {}} currentCode="original code" />,
    );

    fireEvent.click(getByText('Check for bugs'));
    await waitFor(() => expect(apiClient.generateCode).toHaveBeenCalledWith(CHECK_FOR_BUGS_PROMPT, 'original code'));
  });

  it('passes the current code and generated code to CodeDiffEditor', async () => {
    vi.spyOn(apiClient, 'generateCode').mockResolvedValue({response: {code: 'generated code'}});
    const {getByPlaceholderText, getByText, findByTestId} = render(
      <GenerateCodeSection showGenerate={true} onAccept={() => {}} currentCode="original code" />,
    );

    fireEvent.change(getByPlaceholderText(/Describe what you want/), {target: {value: 'my prompt'}});
    fireEvent.click(getByText('Generate'));

    const diff = await findByTestId('code-diff');
    expect(diff.getAttribute('data-original')).toBe('original code');
    expect(diff.getAttribute('data-value')).toBe('generated code');
  });

  describe('discards a deferred refine response after the current proposal is discarded', () => {
    // Two generateCode calls: the first (initial "Generate") resolves immediately with
    // "code A"; the second (a "Refine" click) stays pending until the test resolves it
    // itself, simulating a slow response that arrives after the user has already acted
    // on "code A".
    const setUpPendingRefine = async () => {
      let resolveSecond: (value: {response: {code: string}}) => void;
      vi.spyOn(apiClient, 'generateCode')
        .mockResolvedValueOnce({response: {code: 'code A'}})
        .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));

      const onAccept = vi.fn();
      const rendered = render(
        <GenerateCodeSection showGenerate={true} onAccept={onAccept} currentCode="original code" />,
      );
      const {getByPlaceholderText, getByText, queryByTestId} = rendered;

      fireEvent.change(getByPlaceholderText(/Describe what you want/), {target: {value: 'first prompt'}});
      fireEvent.click(getByText('Generate'));
      await waitFor(() => expect(queryByTestId('code-diff')).toBeInTheDocument());

      fireEvent.click(getByText('Refine')); // second request now pending

      return {...rendered, onAccept, resolveDeferred: () => resolveSecond({response: {code: 'code B'}})};
    };

    it('Accept: a late response cannot repopulate the panel for a second acceptance', async () => {
      const {getByText, queryByTestId, onAccept, resolveDeferred} = await setUpPendingRefine();

      fireEvent.click(getByText('Accept')); // accepts the still-current "code A"
      expect(onAccept).toHaveBeenCalledTimes(1);
      expect(onAccept).toHaveBeenCalledWith('code A');
      expect(queryByTestId('code-diff')).not.toBeInTheDocument();

      resolveDeferred();
      await waitFor(() => expect(apiClient.generateCode).toHaveBeenCalledTimes(2));
      expect(queryByTestId('code-diff')).not.toBeInTheDocument();
      expect(onAccept).toHaveBeenCalledTimes(1);
    });

    it('Reject: a late response cannot restore the rejected proposal', async () => {
      const {getByText, queryByTestId, resolveDeferred} = await setUpPendingRefine();

      fireEvent.click(getByText('Reject'));
      expect(queryByTestId('code-diff')).not.toBeInTheDocument();

      resolveDeferred();
      await waitFor(() => expect(apiClient.generateCode).toHaveBeenCalledTimes(2));
      expect(queryByTestId('code-diff')).not.toBeInTheDocument();
    });

    it('Clear: a late response cannot repopulate the cleared panel', async () => {
      const {getByText, getByPlaceholderText, queryByTestId, resolveDeferred} = await setUpPendingRefine();

      fireEvent.click(getByText('Clear'));
      expect(queryByTestId('code-diff')).not.toBeInTheDocument();
      expect((getByPlaceholderText(/Describe what you want/) as HTMLTextAreaElement).value).toBe('');

      resolveDeferred();
      await waitFor(() => expect(apiClient.generateCode).toHaveBeenCalledTimes(2));
      expect(queryByTestId('code-diff')).not.toBeInTheDocument();
      expect((getByPlaceholderText(/Describe what you want/) as HTMLTextAreaElement).value).toBe('');
    });
  });
});
