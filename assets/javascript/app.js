/**
 * BACKWARD COMPATIBILITY SHIM
 *
 * This file maintains the legacy window.SiteJS global during migration.
 * TODO: Remove this file completely after Phase 6 when all templates are migrated.
 *
 * Migration status: Phase 0 - Compatibility shim active
 */

import Cookies from "./utils/cookies.js";
import { copyToClipboard, copyTextToClipboard } from "./utils/clipboard.js";

// Legacy global namespace for backward compatibility
window.SiteJS = {
  app: {
    Cookies,
    copyToClipboard,
    copyTextToClipboard
  }
};

// Keep named exports for any direct imports (will be removed in Phase 6)
export { Cookies, copyToClipboard, copyTextToClipboard };
