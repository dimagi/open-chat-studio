'use strict'

/**
 * Fraction (0-1) scrolled through a container, given its position and size relative to the
 * viewport. Kept dependency-free (no DOM, no htmx) so it can be unit tested directly.
 */
export function computeProgress({ containerTop, containerHeight, viewportHeight, scrollY }) {
  const scrollable = containerHeight - viewportHeight;
  if (scrollable <= 0) {
    return 0;
  }
  return Math.min(Math.max((scrollY - containerTop) / scrollable, 0), 1);
}
