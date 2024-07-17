import * as JsCookie from "js-cookie"; // generated

// pass-through for Cookies API
export const Cookies = JsCookie.default;

export async function copyToClipboard (callee, elementId) {
  const icon = callee.getElementsByTagName('i')[0]
  const span = callee.getElementsByTagName('span')[0]
  const text = document.getElementById(elementId).innerHTML
  try {
    await navigator.clipboard.writeText(text).then(() => {
      icon.className = 'fa-solid fa-check'
      span.innerHTML = 'Copied!'
    })
  } catch (err) {
    console.error('Failed to copy: ', err)
  }
}
