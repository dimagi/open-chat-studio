import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('widget theme synchronization', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    document.body.innerHTML = '';
    document.documentElement.removeAttribute('data-theme');
    window.matchMedia = vi.fn(() => ({ matches: false, addEventListener: vi.fn() }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('applies the resolved theme to widgets mounted after initial synchronization', async () => {
    localStorage.setItem('theme', 'dark');
    await import('./theme-toggle.js');

    const widget = document.createElement('open-chat-studio-widget');
    document.body.appendChild(widget);
    await Promise.resolve();

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(widget.getAttribute('theme')).toBe('dark');
  });
});
