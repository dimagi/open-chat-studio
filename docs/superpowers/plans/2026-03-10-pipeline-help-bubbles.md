# Pipeline Editor Help Bubbles Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace inline small-text help display in pipeline editor fields with a DaisyUI hover-bubble (question mark icon → popover), shown in both the edit panel and compact node canvas view.

**Architecture:** Add a `HelpBubble` component to `widgets.tsx` mirroring the Django `help.html` template. Update `InputField` to render `HelpBubble` next to the label. Remove the `showHelpText` parameter from `getInputWidget` so help is always passed through.

**Tech Stack:** React, TypeScript, DaisyUI, Font Awesome icons, `npm run dev` for builds, `npm run lint` for linting, `npm run type-check` for TypeScript checking.

**Spec:** `docs/superpowers/specs/2026-03-10-pipeline-help-bubbles-design.md`

---

## Chunk 1: Add `HelpBubble` and update `InputField`

### Task 1: Add `HelpBubble` component

**Files:**
- Modify: `assets/javascript/apps/pipeline/nodes/widgets.tsx` (near `InputField`, around line 1178)

- [ ] **Step 1: Add `HelpBubble` component above `InputField`**

  In `widgets.tsx`, insert the following immediately before the `InputField` function (around line 1178):

  ```tsx
  function HelpBubble({ helpText }: { helpText: string }) {
    if (!helpText) return <></>;
    return (
      <div className="dropdown dropdown-right dropdown-hover">
        <div role="button" className="btn btn-circle btn-ghost btn-xs text-info">
          <i className="text-xs fa-regular fa-circle-question"></i>
        </div>
        <div tabIndex={0} className="card card-sm dropdown-content bg-slate-300 dark:bg-slate-700 rounded-box z-1 w-64 shadow-sm">
          <div className="card-body font-medium text-wrap">
            <p>{helpText}</p>
          </div>
        </div>
      </div>
    );
  }
  ```

- [ ] **Step 2: Update `InputField` to use `HelpBubble`**

  Replace the entire `InputField` function (lines 1178–1195) with:

  ```tsx
  export function InputField({label, help_text, inputError, children}: React.PropsWithChildren<{
    label: string | ReactNode,
    help_text: string,
    inputError?: string | undefined
  }>) {
    return (
      <>
        <div className="fieldset w-full capitalize">
          <div className="flex items-center gap-1">
            <label className="label font-bold">{label}</label>
            <HelpBubble helpText={help_text} />
          </div>
          {children}
        </div>
        <div>
          <small className="text-red-500">{inputError}</small>
        </div>
      </>
    );
  }
  ```

  Note: two things change from the original `InputField`:
  1. The label is now wrapped in `<div className="flex items-center gap-1">` alongside `<HelpBubble>`.
  2. The `<div className="flex flex-col">` wrapper around the error/help becomes a plain `<div>`, and the `<small className="text-muted">{help_text}</small>` line is removed entirely.

- [ ] **Step 3: Migrate `HistoryModeWidget`'s inline help text to `HelpBubble`**

  `HistoryModeWidget` (around line 1140) has a standalone `<small className="text-muted mt-2">` inside `InputField`'s children that renders a dynamic help string based on the current mode. This bypasses `InputField`'s `help_text` prop and would still appear as inline text after Step 2.

  Replace the `InputField` call in `HistoryModeWidget` so the dynamic text flows through `help_text`:

  ```tsx
  // Before:
  <InputField label="History Mode" help_text="">
    <select ...>...</select>
    <small className="text-muted mt-2">{historyModeHelpTexts[historyMode]}</small>
  </InputField>

  // After:
  <InputField label="History Mode" help_text={historyModeHelpTexts[historyMode] ?? ""}>
    <select ...>...</select>
  </InputField>
  ```

  This removes the inline `<small>` and lets `HelpBubble` handle the text instead.

- [ ] **Step 4: Run TypeScript type check**

  ```bash
  npm run type-check assets/javascript/apps/pipeline/nodes/widgets.tsx
  ```

  Expected: no errors related to `HelpBubble` or `InputField`.

- [ ] **Step 5: Run linter**

  ```bash
  npm run lint assets/javascript/apps/pipeline/nodes/widgets.tsx
  ```

  Expected: no new lint errors.

- [ ] **Step 6: Commit**

  ```bash
  git add assets/javascript/apps/pipeline/nodes/widgets.tsx
  git commit -m "feat: add HelpBubble component and update InputField to show help as popover"
  ```

---

## Chunk 2: Remove `showHelpText` gating

### Task 2: Always pass `helpText` in `getInputWidget`

**Files:**
- Modify: `assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx` (around lines 244–312)

- [ ] **Step 1: Remove stale JSDoc comment referencing `showHelpText`**

  In `GetInputWidget.tsx`, find the JSDoc comment above `getInputWidget` (around lines 239–242):

  ```tsx
   * Set `showHelpText` to false to suppress field descriptions (e.g. in the
   * compact node canvas view where space is limited).
  ```

  Delete those two lines from the comment block.

- [ ] **Step 2: Remove `showHelpText` parameter from `getInputWidget`**

  In `GetInputWidget.tsx`, find the `getInputWidget` function signature (around line 244):

  ```tsx
  export const getInputWidget = (
    params: InputWidgetParams,
    getNodeFieldError: (nodeId: string, fieldName: string) => string | undefined,
    readOnly: boolean,
    onHide?: () => void,
    onShow?: () => void,
    showHelpText = true,
  ) => {
  ```

  Remove the `showHelpText = true` parameter:

  ```tsx
  export const getInputWidget = (
    params: InputWidgetParams,
    getNodeFieldError: (nodeId: string, fieldName: string) => string | undefined,
    readOnly: boolean,
    onHide?: () => void,
    onShow?: () => void,
  ) => {
  ```

- [ ] **Step 3: Update `helpText` assignment inside `getInputWidget`**

  Find (around line 299):

  ```tsx
  helpText={showHelpText ? (widgetSchema.description || "") : ""}
  ```

  Replace with:

  ```tsx
  helpText={widgetSchema.description || ""}
  ```

- [ ] **Step 4: Update `getNodeInputWidget` call site**

  In `GetInputWidget.tsx`, find `getNodeInputWidget` (around line 228):

  ```tsx
  return getInputWidget(param, getNodeFieldError, readOnly, undefined, undefined, false);
  ```

  `onHide` and `onShow` are not used here, so drop all trailing arguments:

  ```tsx
  return getInputWidget(param, getNodeFieldError, readOnly);
  ```

- [ ] **Step 5: Run TypeScript type check**

  ```bash
  npm run type-check assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx
  ```

  Expected: no errors.

- [ ] **Step 6: Run linter**

  ```bash
  npm run lint assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx
  ```

  Expected: no new lint errors.

- [ ] **Step 7: Commit**

  ```bash
  git add assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx
  git commit -m "feat: always show help text via bubble, remove showHelpText gating"
  ```

---

## Chunk 3: Build and verify

### Task 3: Full build and visual verification

**Files:** No changes — build and verify only.

- [ ] **Step 1: Run full JS build**

  ```bash
  npm run dev
  ```

  Expected: build completes with no errors.

- [ ] **Step 2: Run type-check across pipeline files**

  ```bash
  npm run type-check assets/javascript/apps/pipeline/
  ```

  Expected: no errors.

- [ ] **Step 3: Manual verification checklist**

  Open the pipeline editor in the browser and verify:
  - [ ] Fields with a `description` in the schema show a `?` icon next to the label
  - [ ] Hovering the `?` icon reveals a popover card with the help text
  - [ ] Fields without a `description` show no `?` icon
  - [ ] The compact node canvas view (nodes on the canvas, not the edit panel) also shows `?` icons for fields with descriptions
  - [ ] No inline `<small>` help text is visible below fields
  - [ ] Dark mode: popover uses `bg-slate-700` correctly
  - [ ] Error messages (red text) still appear below fields as before

- [ ] **Step 4: Final commit if any fixups were needed**

  ```bash
  git add -p
  git commit -m "fix: address build/visual issues with help bubble"
  ```
