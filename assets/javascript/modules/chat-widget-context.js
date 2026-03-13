/**
 * Chat widget page context module.
 * Provides functions to extract and set page context on the chat widget.
 */

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
export function initChatWidgetPageContext(serverContext = {}) {
  const widget = document.querySelector('open-chat-studio-widget');
  if (widget) {
    const clientContext = getClientPageContext();
    widget.pageContext = {
      ...clientContext,
      team: serverContext.team || null,
      activeTab: serverContext.activeTab || null,
      pageTitle: serverContext.pageTitle || clientContext.title,
      messages: serverContext.messages || []
    };
  }
}
