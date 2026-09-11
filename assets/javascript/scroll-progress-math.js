'use strict'

/** Fraction (0-1) through the conversation, given the message on screen and the total count. */
export function computeMessageProgress({ currentIndex, totalMessages }) {
  if (!totalMessages || totalMessages <= 0) {
    return 0;
  }
  return Math.min(Math.max(currentIndex / totalMessages, 0), 1);
}
