/**
 * Chat widget page context module.
 * Provides functions to extract and set page context on the chat widget.
 */

/** Module-level flag to prevent double-patching history methods. */
let _navigationListenerActive = false;

/**
 * Extract client-side page context information for the chat widget.
 * Returns an object with URL, title, hash, and breadcrumbs.
 */
export function getClientPageContext() {
  const path = window.location.pathname;
  const hash = window.location.hash;
  const title = document.title;

  // Extract breadcrumb text from nav elements
  const breadcrumbs = [];
  const breadcrumbNav = document.querySelector('[aria-label="breadcrumbs"]');
  if (breadcrumbNav) {
    const items = breadcrumbNav.querySelectorAll('li');
    items.forEach(item => {
      const text = item.textContent?.trim();
      if (text) {
        breadcrumbs.push(text);
      }
    });
  }

  const context = {
    url: path,
    title: title,
    breadcrumbs: breadcrumbs
  };

  if (hash) {
    context.hash = hash;
  }

  return context;
}

/**
 * Initialize page context on the chat widget element.
 * @param {Object} serverContext - Context passed from Django template
 * @param {string|null} serverContext.team - Team slug
 * @param {string|null} serverContext.activeTab - Current active tab/section
 * @param {string|null} serverContext.pageTitle - Page title
 * @param {Array} serverContext.messages - Django messages [{text, level}]
 */
/**
 * Load widget_page_context from a JSON script tag if present.
 */
function getWidgetPageContext() {
  const el = document.getElementById('widget-page-context');
  if (!el) {
    return {};
  }
  return JSON.parse(el.textContent) || {};
}

export function initChatWidgetPageContext(serverContext = {}) {
  const widget = document.querySelector('open-chat-studio-widget');
  if (widget) {
    const clientContext = getClientPageContext();
    const widgetPageContext = getWidgetPageContext();
    widget.pageContext = {
      ...clientContext,
      team: serverContext.team || null,
      activeTab: serverContext.activeTab || null,
      pageTitle: serverContext.pageTitle || clientContext.title,
      messages: serverContext.messages || [],
      ...widgetPageContext,
    };
  }
}

/**
 * Set up listeners for SPA navigation events so that page context is
 * automatically refreshed on every client-side route change.
 *
 * Patches `history.pushState` and `history.replaceState` to detect
 * programmatic navigations, and also listens for the browser's `popstate`
 * event (back/forward). After each navigation the function re-calls
 * `initChatWidgetPageContext` with the same `serverContext` so the widget
 * receives fresh `url`, `title`, and `breadcrumbs` values.
 *
 * Should be called once per page load — calling it multiple times is safe
 * (subsequent calls are no-ops and log a warning).
 *
 * @param {Object} serverContext - Same server context passed to initChatWidgetPageContext
 * @returns {Function} Cleanup function that removes all listeners and
 *   restores the original history methods.
 */
export function setupNavigationListener(serverContext = {}) {
  if (_navigationListenerActive) {
    console.warn('[open-chat-studio] setupNavigationListener has already been called. It should only be called once per page.');
    return () => {};
  }
  _navigationListenerActive = true;

  const onNavigate = () => {
    initChatWidgetPageContext(serverContext);
    // Dispatch a custom event so the widget can detect the URL change even
    // when no pageContext prop is provided by the host page.
    window.dispatchEvent(new CustomEvent('ocs-navigation'));
  };

  // Patch pushState / replaceState — these are used by all major SPA routers
  // but do not fire a popstate event on their own.
  const originalPushState = history.pushState.bind(history);
  const originalReplaceState = history.replaceState.bind(history);

  history.pushState = function (...args) {
    originalPushState(...args);
    onNavigate();
  };

  history.replaceState = function (...args) {
    originalReplaceState(...args);
    onNavigate();
  };

  window.addEventListener('popstate', onNavigate);

  return () => {
    history.pushState = originalPushState;
    history.replaceState = originalReplaceState;
    window.removeEventListener('popstate', onNavigate);
    _navigationListenerActive = false;
  };
}
