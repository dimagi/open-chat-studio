import {describe, expect, it} from 'vitest';

import './editors.js';

function settle(detail) {
  const errors = [];
  const onError = (event) => {
    errors.push(event.error);
    event.preventDefault();
  };
  window.addEventListener('error', onError);
  document.dispatchEvent(new CustomEvent('htmx:afterSettle', {detail}));
  window.removeEventListener('error', onError);
  return errors;
}

describe('htmx:afterSettle editor reinitialisation', () => {
  it('ignores a settle that has no target', () => {
    expect(settle({})).toEqual([]);
  });

  it('creates editors inside the settled target', () => {
    const target = document.createElement('div');
    target.innerHTML = '<div class="diff-view" data-diff-a="a" data-diff-b="b"></div>';
    document.body.appendChild(target);

    expect(settle({target})).toEqual([]);
    expect(target.querySelector('.cm-mergeView')).not.toBeNull();
  });
});
