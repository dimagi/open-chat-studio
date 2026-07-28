import * as JsCookie from "js-cookie"; // generated

// pass-through for Cookies API
export const Cookies = JsCookie.default;

// Shared dynamic-filter CSV wire-format helpers, exposed on SiteJS.app for inline template JS
// (e.g. the Alpine filter component). Webpack modules should import from ./filters/csvTilde.js.
export {serializeCSVTildeValues, parseCSVTildeValue} from "./filters/csvTilde.js";

export async function copyToClipboard (callee, elementId) {
  const element = document.getElementById(elementId)
  if (!element) return;
  let text;
  if (element.tagName === "INPUT") {
    text = element.value;
  } else {
    text = element.innerHTML;
  }
  await copyTextToClipboard(callee, text);
}

export async function copyTextToClipboard (callee, text) {
  try {
    await navigator.clipboard.writeText(text).then(() => {
      const prevHTML = callee.innerHTML
      callee.disabled = true;
      callee.innerHTML = '<span><i class="fa-solid fa-check"></i>Copied!</span>'
      setTimeout(() => {
        callee.innerHTML = prevHTML;
        callee.disabled = false;
      }, 2000);
    })
  } catch (err) {
    console.error('Failed to copy: ', err)
  }
}
