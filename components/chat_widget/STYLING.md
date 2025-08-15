# Widget Styling Guide

## Goals

* The widget should work well on various screen sizes, including mobile.
* The widget style should be customizable using custom CSS.

## Approach

Not all styles need to be customizable. Mostly it is just font sizes and colors. The sizing of elements should be based
on the font size of the root element (`--chat-window-font-size`). 

* Font size should only be specified on root elements and elements that need a different font size, e.g. small text
* Use `em` for sizes that should change with the font size (e.g. icon width / height) and `px` for absolute sizes (e.g. border, gap) 

## CSS Style
* Always use existing Tailwind classes over raw CSS, e.g. `w-screen` instead of `width: 100vw`
* Never use custom Tailwind classes with vars except for media classes
  * ❌ `w-[var(--width)]` 
  * ✔️ `width: var(--width)` 
  * ✔️ `md:w-[var(--width)]`
    * Media query with custom Tailwind class is accpetable to avoid having to have a separate media query section 