'use strict'
import htmx from 'htmx.org'
import { computeProgress } from './scroll-progress-math.js'

/**
 * Tracks scroll progress through `containerSelector` and reflects it as the width of
 * `#scroll-progress-bar`. The container itself loads asynchronously (htmx `intersect once`)
 * and can be replaced wholesale later by a filter change, so it's re-queried on every update
 * rather than cached once at setup time, and also re-checked after every htmx swap.
 */
export function setupScrollProgress(containerSelector) {
  const bar = document.getElementById('scroll-progress-bar');
  if (!bar) {
    return;
  }

  const update = () => {
    const container = document.querySelector(containerSelector);
    if (!container) {
      return;
    }
    const rect = container.getBoundingClientRect();
    const progress = computeProgress({
      containerTop: window.scrollY + rect.top,
      containerHeight: container.scrollHeight,
      viewportHeight: window.innerHeight,
      scrollY: window.scrollY,
    });
    bar.style.width = `${progress * 100}%`;
  };

  let ticking = false;
  const onScroll = () => {
    if (ticking) {
      return;
    }
    ticking = true;
    requestAnimationFrame(() => {
      update();
      ticking = false;
    });
  };

  window.addEventListener('scroll', onScroll, { passive: true });
  htmx.on('htmx:afterSwap', update);
  update();
}

export default { setupScrollProgress };
