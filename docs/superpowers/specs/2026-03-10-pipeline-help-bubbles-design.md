# Pipeline Editor Help Bubbles

**Date:** 2026-03-10
**Status:** Approved

## Summary

Replace inline `<small>` help text in pipeline editor fields with a DaisyUI hover-bubble (question mark icon → popover). Display the bubble in both the edit panel and the compact node canvas view.

## Components

### `HelpBubble` (new, in `widgets.tsx`)

- Props: `helpText: string`
- Returns empty fragment when `helpText` is empty
- Mirrors `templates/generic/help.html`: DaisyUI `dropdown dropdown-right dropdown-hover` with a `fa-regular fa-circle-question` icon
- Popover is a 64-unit-wide card using `bg-slate-300 dark:bg-slate-700`

### `InputField` (modified, in `widgets.tsx`)

- Label row becomes `flex items-center gap-1` containing the label and `<HelpBubble helpText={help_text} />`
- Remove `<small className="text-muted">{help_text}</small>`
- Keep `<small className="text-red-500">{inputError}</small>` for validation errors

### `getInputWidget` (modified, in `GetInputWidget.tsx`)

- Always pass `widgetSchema.description || ""` as `helpText` — remove the `showHelpText` conditional
- Remove the `showHelpText` parameter from the function signature
- Remove the `false` argument from the `getNodeInputWidget` call site
