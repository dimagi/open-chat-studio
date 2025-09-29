const next = (el: Element, selector: string) => {
  let sibling = el.nextElementSibling
  if (!selector) return sibling
  while (sibling) {
    if (sibling.matches(selector)) return sibling
    sibling = sibling.nextElementSibling
  }
  return null
}

const previous = (el: Element, selector: string) => {
  let sibling = el.previousElementSibling
  if (!selector) return sibling
  while (sibling) {
    if (sibling.matches(selector)) return sibling
    sibling = sibling.previousElementSibling
  }
  return null
}

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
    return previous(el, selector.split('previous')[1]);
  }
  return document.querySelector(selector);
}
