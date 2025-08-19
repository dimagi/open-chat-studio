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
  const currentDomain = window.location.hostname;
  const apiDomain = getDomainFromUrl(apiBaseUrl);

  if (!apiDomain) {
    return false;
  }

  return currentDomain === apiDomain;
}

function getDomainFromUrl(url: string): string | null {
  try {
    const urlObj = new URL(url);
    return urlObj.hostname;
  } catch (error) {
    return null;
  }
}
