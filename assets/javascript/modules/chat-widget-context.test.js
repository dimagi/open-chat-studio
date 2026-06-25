/**
 * Tests for chat-widget-context.js
 */

// Reset modules before each test so module-level state (_navigationListenerActive) is fresh.
beforeEach(() => {
  jest.resetModules();
  // Provide a minimal DOM environment
  document.body.innerHTML = '';
});

afterEach(() => {
  jest.restoreAllMocks();
});

function makeWidget(pageContext = null) {
  const widget = document.createElement('open-chat-studio-widget');
  if (pageContext !== null) {
    widget.pageContext = pageContext;
  }
  document.body.appendChild(widget);
  return widget;
}

describe('getClientPageContext', () => {
  it('returns url, title, and empty breadcrumbs when no nav element present', async () => {
    const { getClientPageContext } = await import('./chat-widget-context.js');
    document.title = 'Test Page';

    const ctx = getClientPageContext();

    expect(ctx.title).toBe('Test Page');
    expect(typeof ctx.url).toBe('string');
    expect(Array.isArray(ctx.breadcrumbs)).toBe(true);
    expect(ctx.breadcrumbs).toHaveLength(0);
  });

  it('extracts breadcrumbs from aria-label="breadcrumbs" nav', async () => {
    const { getClientPageContext } = await import('./chat-widget-context.js');

    document.body.innerHTML = `
      <nav aria-label="breadcrumbs">
        <ol><li>Home</li><li>Settings</li></ol>
      </nav>
    `;

    const ctx = getClientPageContext();

    expect(ctx.breadcrumbs).toEqual(['Home', 'Settings']);
  });
});

describe('initChatWidgetPageContext', () => {
  it('sets pageContext on the widget element', async () => {
    const { initChatWidgetPageContext } = await import('./chat-widget-context.js');
    const widget = makeWidget();
    document.title = 'My Page';

    initChatWidgetPageContext({ team: 'my-team', activeTab: 'chat', pageTitle: null, messages: [] });

    expect(widget.pageContext).toBeDefined();
    expect(widget.pageContext.team).toBe('my-team');
    expect(widget.pageContext.activeTab).toBe('chat');
  });

  it('does nothing when no widget element is present', async () => {
    const { initChatWidgetPageContext } = await import('./chat-widget-context.js');
    // No widget in DOM — should not throw
    expect(() => initChatWidgetPageContext({})).not.toThrow();
  });
});

describe('setupNavigationListener', () => {
  it('re-initializes page context on popstate', async () => {
    const { initChatWidgetPageContext, setupNavigationListener } = await import('./chat-widget-context.js');
    const widget = makeWidget();

    setupNavigationListener({ team: 'acme' });

    // Simulate browser back/forward
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(widget.pageContext).toBeDefined();
    expect(widget.pageContext.team).toBe('acme');
  });

  it('dispatches ocs-navigation custom event on popstate', async () => {
    const { setupNavigationListener } = await import('./chat-widget-context.js');
    makeWidget();

    const spy = jest.fn();
    window.addEventListener('ocs-navigation', spy);

    setupNavigationListener({});
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(spy).toHaveBeenCalledTimes(1);
    window.removeEventListener('ocs-navigation', spy);
  });

  it('patches history.pushState to trigger context update', async () => {
    const { setupNavigationListener } = await import('./chat-widget-context.js');
    const widget = makeWidget();

    setupNavigationListener({ team: 'spa-team' });

    history.pushState({}, '', '/new-page');

    expect(widget.pageContext).toBeDefined();
    expect(widget.pageContext.team).toBe('spa-team');
  });

  it('patches history.replaceState to trigger context update', async () => {
    const { setupNavigationListener } = await import('./chat-widget-context.js');
    const widget = makeWidget();

    setupNavigationListener({ team: 'replace-team' });

    history.replaceState({}, '', '/replaced-page');

    expect(widget.pageContext).toBeDefined();
    expect(widget.pageContext.team).toBe('replace-team');
  });

  it('cleanup function restores original history methods and removes listeners', async () => {
    const { setupNavigationListener } = await import('./chat-widget-context.js');
    const widget = makeWidget();

    const originalPushState = history.pushState;
    const cleanup = setupNavigationListener({});

    expect(history.pushState).not.toBe(originalPushState);

    cleanup();

    expect(history.pushState).toBe(originalPushState);

    // After cleanup, pushState should not update the widget
    widget.pageContext = undefined;
    history.pushState({}, '', '/after-cleanup');
    expect(widget.pageContext).toBeUndefined();
  });

  it('warns and returns a no-op when called a second time', async () => {
    const { setupNavigationListener } = await import('./chat-widget-context.js');
    makeWidget();

    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});

    setupNavigationListener({});
    const cleanup2 = setupNavigationListener({});

    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(typeof cleanup2).toBe('function');
    // cleanup2 should be a no-op (calling it shouldn't throw)
    expect(() => cleanup2()).not.toThrow();
  });
});
