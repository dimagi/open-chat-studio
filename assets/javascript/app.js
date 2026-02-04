import * as JsCookie from "js-cookie"; // generated

// pass-through for Cookies API
export const Cookies = JsCookie.default;

export async function copyToClipboard (callee, elementId) {
  const element = document.getElementById(elementId)
  let text;
  if (element.tagName === "INPUT") {
    text = element.value;
  } else {
    text = element.innerHTML;
  }
  await copyTextToClipboard(callee, text);
}

export async function copyTextToClipboard (callee, text) {
  try {
    await navigator.clipboard.writeText(text).then(() => {
      const prevHTML = callee.innerHTML
      callee.innerHTML = '<span><i class="fa-solid fa-check"></i>Copied!</span>'
      setTimeout(() => {
        callee.innerHTML = prevHTML;
      }, 2000);
    })
  } catch (err) {
    console.error('Failed to copy: ', err)
  }
}

/**
 * Extract page context information for the chat widget.
 * Returns an object with URL, title, team, section, and breadcrumbs.
 */
export function getPageContext() {
  const path = window.location.pathname;
  const hash = window.location.hash;
  const title = document.title;

  // Extract team and section from URL pattern: /a/{team}/{section}/...
  const urlMatch = path.match(/^\/a\/([^/]+)(?:\/([^/]+))?/);
  const team = urlMatch ? urlMatch[1] : null;
  const section = urlMatch ? urlMatch[2] : null;

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
    team: team,
    section: section,
    breadcrumbs: breadcrumbs
  };

  if (hash) {
    context.hash = hash;
  }

  return context;
}

/**
 * Initialize page context on the chat widget element.
 */
export function initChatWidgetPageContext() {
  const widget = document.querySelector('open-chat-studio-widget');
  if (widget) {
    widget.pageContext = getPageContext();
  }
}
