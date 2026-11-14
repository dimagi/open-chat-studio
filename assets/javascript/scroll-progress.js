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

/** Reflects which message is on screen, relative to the total, as the width of `#scroll-progress-fill`. */
export function setupScrollProgress(containerSelector) {
  const bar = document.getElementById('scroll-progress-fill');
  const progressContainer = document.getElementById('scroll-progress-bar');
  if (!bar || !progressContainer) {
    return;
  }

  const setProgress = (progress) => {
    const percentage = Math.round(progress * 100);
    bar.style.width = `${percentage}%`;
    progressContainer.setAttribute('aria-valuenow', `${percentage}`);
    progressContainer.setAttribute('aria-valuetext', `${percentage} percent`);
  };

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
      setProgress(1);
      return;
    }
    if (window.scrollY <= 0) {
      setProgress(0);
      return;
    }
    const totalMessages = parseInt(container.dataset.totalMessages, 10) || 0;
    const rows = container.querySelectorAll('[data-message-index]');
    const currentIndex = currentMessageIndex(rows);
    const progress = currentIndex === null ? 0 : computeMessageProgress({ currentIndex, totalMessages });
    setProgress(progress);
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

  let observedContainer = null;
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null;
  const observeContainer = () => {
    const container = document.querySelector(containerSelector);
    if (container && container !== observedContainer) {
      if (observedContainer) {
        resizeObserver?.disconnect();
      }
      resizeObserver?.observe(container);
      observedContainer = container;
    }
    update();
  };
  const onHtmxSettle = () => observeContainer();
  const onPageHide = () => {
    window.removeEventListener('scroll', onScroll);
    htmx.off('htmx:afterSettle', onHtmxSettle);
    resizeObserver?.disconnect();
  };

  window.addEventListener('scroll', onScroll, { passive: true });
  htmx.on('htmx:afterSettle', onHtmxSettle);
  window.addEventListener('pagehide', onPageHide, { once: true });
  observeContainer();
}

export default { setupScrollProgress };
