import Cookies from "js-cookie";


/**
 * Get CSRF token from cookies if the current domain matches the API base URL
 */
export function getCSRFToken(apiBaseUrl: string): string | undefined {
  if (!currentDomainMatchesApiBaseUrl(apiBaseUrl)) {
    return undefined;
  }

  return Cookies.get('csrftoken')
}

function currentDomainMatchesApiBaseUrl(apiBaseUrl: string): boolean {
  let apiBase: URL;
  try {
    apiBase = new URL(apiBaseUrl);
  } catch {
    return false;
  }

  return window.location.hostname === apiBase.hostname && window.location.port === apiBase.port && window.location.protocol === apiBase.protocol;
}
