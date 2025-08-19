# Widget Styling Guide

## Goals

* The widget should work well on various screen sizes, including mobile.
* The widget style should be customizable using custom CSS.

## Approach

Not all styles need to be customizable. Mostly it is just font sizes and colors. The sizing of elements should be based
on the font size of the root element (`--chat-window-font-size`). 

* Font size should only be specified on root elements and elements that need a different font size, e.g. small text.
* Use `em` for sizes that should change with the font size (e.g. icon width / height) and `px` for absolute sizes (e.g. border, gap).

## CSS Style
* Never mix inline Tailwind classes with custom classes. If a custom class is needed, move all stying to the custom class.
  * ❌ `<div class="w-full custom-class">` - Move `w-full` to the `custom-class` definition.
* Always use existing Tailwind classes over raw CSS.
  * ❌ `width: 100vw`
  * ✔ `w-screen`
* Never use custom Tailwind classes with vars except for media classes.
  * ❌ `w-[var(--width)]` 
  * ✔️ `width: var(--width)` 
  * ✔️ `md:w-[var(--width)]`
    * Media query with custom Tailwind class is acceptable to avoid having to have a separate media query section.
* Use other vars as default values instead of repeating values.
  * ✔️ `--element-bg-color: var(--chat-window-bg-color)`
    * Remains consistent with the main window color but allows overriding if necessary.
* Never create nested variable dependencies.
    ```css
    /* Don't do this */
    --other-bg-color: var(--chat-window-bg-color);
    --element-bg-color: var(--other-bg-color);
    ```