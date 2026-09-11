'use strict'
import htmx from 'htmx.org'
import { computeMessageProgress } from './scroll-progress-math.js'

/** The topmost message row still on screen -- its `data-message-index`, or null if none rendered. */
function currentMessageIndex(rows) {
  for (const row of rows) {
    if (row.getBoundingClientRect().bottom > 0) {
      return parseInt(row.dataset.messageIndex, 10);
    }
  }
  const last = rows[rows.length - 1];
  return last ? parseInt(last.dataset.messageIndex, 10) : null;
}

/** Reflects which message is on screen, relative to the total, as the width of `#scroll-progress-bar`. */
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
    // The viewport shows several rows at once, so "the topmost visible row" undershoots the
    // true last message once scrolled to the bottom (its neighbors above it are still what's
    // topmost). Clamp the two ends explicitly rather than relying on row lookup to hit them.
    const atBottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 1;
    if (atBottom) {
      bar.style.width = '100%';
      return;
    }
    if (window.scrollY <= 0) {
      bar.style.width = '0%';
      return;
    }
    const totalMessages = parseInt(container.dataset.totalMessages, 10) || 0;
    const rows = container.querySelectorAll('[data-message-index]');
    const currentIndex = currentMessageIndex(rows);
    const progress = currentIndex === null ? 0 : computeMessageProgress({ currentIndex, totalMessages });
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
