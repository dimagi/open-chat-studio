/**
 * Find next sibling element matching selector
 */
const next = (el: Element, selector: string) => {
  let sibling = el.nextElementSibling
  if (!selector) return sibling
  while (sibling) {
    if (sibling.matches(selector)) return sibling
    sibling = sibling.nextElementSibling
  }
  return null
}

/**
 * Find previous sibling element matching selector
 */
const previous = (el: Element, selector: string) => {
  let sibling = el.previousElementSibling
  if (!selector) return sibling
  while (sibling) {
    if (sibling.matches(selector)) return sibling
    sibling = sibling.previousElementSibling
  }
  return null
}

/**
 * Enhanced element finder with special selector prefixes:
 * - 'closest selector' - finds closest ancestor matching selector
 * - 'next selector' - finds next sibling matching selector
 * - 'previous selector' - finds previous sibling matching selector
 * - otherwise uses document.querySelector
 */
export const find = (el: Element, selector: string) => {
  if (!selector) return null;
  if (selector.startsWith('closest ')) {
    selector = selector.split('closest ')[1];
    return el.closest(selector);
  }
  if (selector.startsWith('next ')) {
    return next(el, selector.split('next ')[1]);
  }
  if (selector.startsWith('previous ')) {
    return previous(el, selector.split('previous ')[1]);
  }
  return document.querySelector(selector);
}
