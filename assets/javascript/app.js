import * as JsCookie from "js-cookie"; // generated

// pass-through for Cookies API
export const Cookies = JsCookie.default;

export async function copyToClipboard (callee, elementId) {
  const icon = callee.getElementsByTagName('i')[0]
  const span = callee.getElementsByTagName('span')[0]
  const element = document.getElementById(elementId)
  let text;
  if (element.tagName === "INPUT") {
    text = element.value;
  } else {
    text = element.innerHTML;
  }
  
  try {
    await navigator.clipboard.writeText(text).then(() => {
      const prevClass = icon.className;
      const prevHTML = span.innerHTML
      icon.className = 'fa-solid fa-check'
      span.innerHTML = 'Copied!'
      setTimeout(() => {
        icon.className = prevClass;
        span.innerHTML = prevHTML;
      }, 2000);
    })
  } catch (err) {
    console.error('Failed to copy: ', err)
  }
}
