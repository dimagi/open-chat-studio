/**
 * Clipboard utilities for copying text to clipboard
 *
 * Usage:
 *   import { copyToClipboard, copyTextToClipboard } from './utils/clipboard.js'
 *
 *   // Copy from an element by ID
 *   await copyToClipboard(buttonElement, 'element-id')
 *
 *   // Copy text directly
 *   await copyTextToClipboard(buttonElement, 'text to copy')
 */

export async function copyToClipboard(callee, elementId) {
  const element = document.getElementById(elementId);
  let text;
  if (element.tagName === "INPUT") {
    text = element.value;
  } else {
    text = element.innerHTML;
  }
  await copyTextToClipboard(callee, text);
}

export async function copyTextToClipboard(callee, text) {
  try {
    await navigator.clipboard.writeText(text).then(() => {
      const prevHTML = callee.innerHTML;
      callee.innerHTML = '<span><i class="fa-solid fa-check"></i>Copied!</span>';
      setTimeout(() => {
        callee.innerHTML = prevHTML;
      }, 2000);
    });
  } catch (err) {
    console.error('Failed to copy: ', err);
  }
}
