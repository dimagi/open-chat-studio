/**
 * Alpine.js utility plugin
 *
 * Registers magic properties for common utilities:
 * - $cookies: Access to js-cookie API
 * - $clipboard: Clipboard copy utilities
 *
 * NOTE: tokenCounter store is NOT registered here to avoid loading tiktoken globally.
 * It's only needed in prompt_builder.html and should be registered page-specifically.
 */

import Cookies from '../utils/cookies.js';
import { copyToClipboard, copyTextToClipboard } from '../utils/clipboard.js';

export default function (Alpine) {
    // Magic: $cookies
    // Usage: this.$cookies.get('csrftoken')
    Alpine.magic('cookies', () => Cookies);

    // Magic: $clipboard
    // Usage: this.$clipboard.copy($el, 'element-id')
    // Usage: this.$clipboard.copyText($el, 'text')
    Alpine.magic('clipboard', () => ({
        copy: copyToClipboard,
        copyText: copyTextToClipboard
    }));
}
